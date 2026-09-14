"""`POST /resumes` is hard-gated on a validated per-user LLM configuration
— docs/llm-provider-config/TECH.md Task 7.

These tests deliberately do *not* stub `get_user_llm_client`: the whole
point of the gate is the distinction between "no row at all" and "a row that
was saved but never validated live against the provider", and stubbing the
factory would collapse both into one indistinguishable exception. So they
run the real `UserLlmConfigRepository` against a real (SQLite in-memory)
`user_llm_config` table, wired in through
`infrastructure.llm.user_client.set_session_factory` — the same DI seam the
consumer-side components use. No PostgreSQL, Kafka broker, or live provider
is required; `groq.Groq(...)` opens no connection until a completion is
actually requested.

Requests go through `httpx.ASGITransport` rather than `TestClient` so the
app runs on the same event loop as the async fixture that seeded the
database (a `TestClient` portal would run it on a different loop, which
SQLAlchemy's async engine does not allow). Starlette awaits a response's
`BackgroundTasks` before returning, so background parsing still completes
before the assertions, exactly as it does under `TestClient`.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
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
from profiles.api.dependencies import get_profile_service
from profiles.api.routes import LLM_NOT_CONFIGURED_MESSAGE, router
from profiles.service import ProfileService
from shared.types.domain.user import User
from shared.types.domain.user_llm_config import LlmProviderName
from shared.types.ids import UserId
from tests.profiles.conftest import (
    FakeCandidateProfileRepository,
    FakeResumeRepository,
    llm_response,
    make_llm_client,
)

pytestmark = pytest.mark.asyncio

API_KEY = "gsk-test-key-not-a-real-credential"

_FIELDS: dict[str, Any] = {
    "title": "Registered Nurse",
    "summary": "Cares for patients.",
    "skills": ["Triage"],
    "experience_years": 4.0,
    "seniority": "Senior",
    "education": [],
    "certifications": ["RN"],
    "projects": [],
    "industries": ["Healthcare"],
    "target_roles": ["Charge Nurse"],
}

_UPLOAD = {
    "file_name": "resume.txt",
    "file_content": base64.b64encode(b"Jane Doe\nRegistered Nurse").decode("ascii"),
}


@dataclass
class Harness:
    app: FastAPI
    client: httpx.AsyncClient
    session_factory: async_sessionmaker[AsyncSession]
    user_id: UserId
    resumes: FakeResumeRepository
    profiles: FakeCandidateProfileRepository

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


@pytest_asyncio.fixture
async def harness(
    monkeypatch: pytest.MonkeyPatch, event_producer, resume_storage
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
            tables=[UserRecord.__table__, UserLlmConfigRecord.__table__],
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    user_client.set_session_factory(session_factory)

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

    resumes = FakeResumeRepository()
    profiles = FakeCandidateProfileRepository()
    llm_client, _ = make_llm_client([llm_response(_FIELDS)])
    service = ProfileService(
        resumes,
        profiles,
        producer=event_producer,
        storage=resume_storage,
        llm_client=llm_client,
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_profile_service] = lambda: service
    app.dependency_overrides[get_current_user_id] = lambda: user_id

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield Harness(
            app=app,
            client=client,
            session_factory=session_factory,
            user_id=user_id,
            resumes=resumes,
            profiles=profiles,
        )

    user_client.set_session_factory(None)
    await engine.dispose()


def _assert_gated(response: httpx.Response) -> None:
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "LLM_NOT_CONFIGURED"
    assert detail["message"] == LLM_NOT_CONFIGURED_MESSAGE
    assert detail["message"] == "Add your AI provider key in Settings to use this feature."


async def test_post_resumes_is_gated_when_the_user_has_no_config(
    harness: Harness, fake_producer_client
) -> None:
    response = await harness.client.post("/resumes", json=_UPLOAD)

    _assert_gated(response)
    # Gated *before* any work: nothing stored, nothing parsed, nothing published.
    assert harness.resumes.by_id == {}
    assert harness.profiles.by_id == {}
    assert fake_producer_client.produced == []


async def test_post_resumes_is_gated_when_the_config_was_never_validated(
    harness: Harness, fake_producer_client
) -> None:
    """A saved-but-never-validated row is the same thing to the user as no
    row at all: the key behind it has never been proven to work, so the
    action must still be blocked rather than failing later in parsing.
    """
    await harness.save_llm_config(last_validated_at=None)

    response = await harness.client.post("/resumes", json=_UPLOAD)

    _assert_gated(response)
    assert harness.resumes.by_id == {}
    assert harness.profiles.by_id == {}
    assert fake_producer_client.produced == []


async def test_post_resumes_succeeds_for_a_validly_configured_user(
    harness: Harness,
) -> None:
    await harness.save_llm_config(last_validated_at=datetime.now(UTC))

    response = await harness.client.post("/resumes", json=_UPLOAD)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["user_id"] == str(harness.user_id)
    assert body["file_name"] == "resume.txt"
    # Unchanged end-to-end behavior: the resume is stored and background
    # parsing produced a profile, exactly as before the gate existed.
    assert len(harness.resumes.by_id) == 1
    assert len(harness.profiles.by_id) == 1


async def test_unauthenticated_upload_still_returns_401_not_the_gate_error(
    harness: Harness,
) -> None:
    """The gate resolves the caller's identity from the same token the route
    already required, so a request with no token must still fail
    authentication first — this task must not turn a 401 into a 400.
    """
    harness.app.dependency_overrides.pop(get_current_user_id)  # no override -> real dependency

    response = await harness.client.post("/resumes", json=_UPLOAD)

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


async def test_unrelated_routes_are_not_gated(harness: Harness) -> None:
    """Only `POST /resumes` is gated — the four routes on this router that
    never trigger an LLM call keep working for a user with no key.
    """
    assert (await harness.client.get("/resumes")).status_code == 200
    assert (await harness.client.get("/profiles")).status_code == 200
