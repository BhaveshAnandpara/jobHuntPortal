"""`POST /jobs/{job_id}/tailored-resume` is hard-gated on a validated
per-user LLM configuration — docs/tailored-resume/TECH.md Task 6, reusing
docs/llm-provider-config/ Task 7's pattern.

Like `tests/profiles/test_llm_gating.py` and
`tests/jobs/ingestion/test_llm_gating.py`, these tests run the real
`UserLlmConfigRepository` against a real (SQLite in-memory)
`user_llm_config` table rather than stubbing `get_user_llm_client`: "no row
at all" and "a row saved but never validated" are two distinct states the
gate must treat identically, and stubbing the factory would make them
indistinguishable. The DI seam is
`infrastructure.llm.user_client.set_session_factory`. No PostgreSQL, Kafka
broker, browser, or live provider is required.

The two read routes are asserted *not* to be gated: retrieving a document
that already exists spends nobody's LLM quota, so a user whose key was
later removed must still be able to download what they already have.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from infrastructure.auth.dependencies import get_current_user_id
from infrastructure.llm import user_client
from infrastructure.security.encryption import ENV_VAR
from profiles.repository import to_resume_profile
from resume_tailoring import db as tailoring_db
from resume_tailoring.api import (
    LLM_NOT_CONFIGURED_MESSAGE,
    get_document_naming,
    get_storage,
    get_tailor_resume,
    router,
)
from resume_tailoring.models import TailoredResumeRecord
from resume_tailoring.repository import TailoredResumeRepository
from resume_tailoring.service import TailoringResult
from resume_tailoring.storage import LocalTailoredResumeStorage
from shared.types.domain.user import User
from shared.types.domain.user_llm_config import LlmProviderName
from shared.types.ids import JobId, UserId
from tests.resume_tailoring.conftest import make_candidate_profile, naming_override
from tests.resume_tailoring.test_api import ScriptedTailoring

pytestmark = pytest.mark.asyncio

API_KEY = "gsk-test-key-not-a-real-credential"
BASE_SCORE = 0.62
ACHIEVED_SCORE = 0.81


@dataclass
class Harness:
    app: FastAPI
    client: httpx.AsyncClient
    session_factory: async_sessionmaker[AsyncSession]
    user_id: UserId
    job_id: JobId
    storage_dir: Path
    tailoring: ScriptedTailoring

    async def save_llm_config(self, *, last_validated_at: datetime | None) -> None:
        """Write a real `user_llm_config` row for this harness's user."""
        from users.repository import UserLlmConfigRepository

        async with self.session_factory() as session:
            await UserLlmConfigRepository(session).upsert(
                user_id=self.user_id,
                provider=LlmProviderName.GROQ,
                model=None,
                api_key=API_KEY,
                last_validated_at=last_validated_at,
            )
            await session.commit()

    async def stored_row(self):
        async with self.session_factory() as session:
            return await TailoredResumeRepository(session).get_for_job(
                self.user_id, self.job_id
            )

    def stored_files(self) -> list[Path]:
        return sorted(p for p in self.storage_dir.rglob("*") if p.is_file())

    async def generate(self) -> httpx.Response:
        return await self.client.post(f"/jobs/{self.job_id}/tailored-resume")


@pytest_asyncio.fixture
async def harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Harness]:
    pytest.importorskip(
        "aiosqlite", reason="async sqlite driver not installed; gating tests need it"
    )
    # A fresh, test-only Fernet key per test — never a real secret.
    monkeypatch.setenv(ENV_VAR, Fernet.generate_key().decode())

    from infrastructure.database import Base
    from users.models import UserLlmConfigRecord, UserRecord
    from users.repository import UserRepository

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            tables=[
                UserRecord.__table__,
                UserLlmConfigRecord.__table__,
                TailoredResumeRecord.__table__,
            ],
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    # One engine serves both seams: the gate reads `user_llm_config`
    # through `infrastructure.llm.user_client`, the routes read/write
    # `tailored_resumes` through this component's own `db.py`.
    user_client.set_session_factory(session_factory)
    tailoring_db.set_session_factory(session_factory)

    user_id = UserId(uuid4())
    async with session_factory() as session:
        await UserRepository(session).add(
            User(
                id=user_id,
                email=f"{user_id}@example.com",
                display_name="Test User",
                created_at=datetime.now(UTC),
                timezone="UTC",
            ),
            "not-a-real-hash",
        )
        await session.commit()

    storage_dir = tmp_path / "tailored_resumes"
    storage = LocalTailoredResumeStorage(storage_dir)
    tailoring = ScriptedTailoring()
    tailoring.result = TailoringResult.improvement(
        base_score=BASE_SCORE,
        achieved_score=ACHIEVED_SCORE,
        tailored_profile=to_resume_profile(make_candidate_profile(user_id)),
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_tailor_resume] = lambda: tailoring
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    # This file is about the LLM-config gate, not about filenames; the real
    # naming dependency would need two other components' tables.
    app.dependency_overrides[get_document_naming] = naming_override()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield Harness(
            app=app,
            client=client,
            session_factory=session_factory,
            user_id=user_id,
            job_id=JobId(uuid4()),
            storage_dir=storage_dir,
            tailoring=tailoring,
        )

    user_client.set_session_factory(None)
    tailoring_db.set_session_factory(None)
    await engine.dispose()


def _assert_gated(response: httpx.Response) -> None:
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "LLM_NOT_CONFIGURED"
    assert detail["message"] == LLM_NOT_CONFIGURED_MESSAGE
    assert detail["message"] == "Add your AI provider key in Settings to use this feature."


async def test_generation_is_gated_when_the_user_has_no_config(
    harness: Harness,
) -> None:
    response = await harness.generate()

    _assert_gated(response)
    # Gated *before* any work: no LLM call was made, no document was
    # written, and no row was inserted.
    assert harness.tailoring.calls == []
    assert harness.stored_files() == []
    assert await harness.stored_row() is None


async def test_generation_is_gated_when_the_config_was_never_validated(
    harness: Harness,
) -> None:
    """A saved-but-never-validated row means the key behind it has never
    been proven to work, so generation is blocked exactly as if no row
    existed — the user still has something to fix in Settings.
    """
    await harness.save_llm_config(last_validated_at=None)

    response = await harness.generate()

    _assert_gated(response)
    assert harness.tailoring.calls == []
    assert harness.stored_files() == []
    assert await harness.stored_row() is None


async def test_generation_succeeds_for_a_validly_configured_user(
    harness: Harness,
) -> None:
    await harness.save_llm_config(last_validated_at=datetime.now(UTC))

    response = await harness.generate()

    assert response.status_code == 201, response.text
    assert response.json()["improved"] is True
    assert harness.tailoring.calls == [(harness.job_id, harness.user_id)]
    assert len(harness.stored_files()) == 1


async def test_unauthenticated_request_still_returns_401_not_the_gate_error(
    harness: Harness,
) -> None:
    """The gate reads the caller's identity from the token the route already
    required, so an unauthenticated request must still fail authentication
    first — the gate must not turn a 401 into a 400.
    """
    harness.app.dependency_overrides.pop(get_current_user_id)

    response = await harness.generate()

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


async def test_the_read_routes_are_not_gated(harness: Harness) -> None:
    """With a validated key the user generates a document; after the key
    stops being valid, both read routes still work. Generation itself is
    blocked again, which is the only thing the gate is for.
    """
    await harness.save_llm_config(last_validated_at=datetime.now(UTC))
    assert (await harness.generate()).status_code == 201

    # The provider configuration stops being usable (key replaced in
    # Settings and not yet re-validated) — the same state the gate blocks.
    await harness.save_llm_config(last_validated_at=None)

    metadata = await harness.client.get(f"/jobs/{harness.job_id}/tailored-resume")
    download = await harness.client.get(
        f"/jobs/{harness.job_id}/tailored-resume/download"
    )

    assert metadata.status_code == 200, metadata.text
    assert download.status_code == 200, download.text
    assert download.content.startswith(b"PK\x03\x04")
    _assert_gated(await harness.generate())
