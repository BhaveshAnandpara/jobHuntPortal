"""`POST /jobs/ingest-url` is hard-gated on a validated per-user LLM
configuration — docs/llm-provider-config/TECH.md Task 7.

Like `tests/profiles/test_llm_gating.py`, these tests run the real
`UserLlmConfigRepository` against a real (SQLite in-memory)
`user_llm_config` table rather than stubbing `get_user_llm_client`: "no row
at all" and "a row saved but never validated" are two distinct states the
gate must treat identically, and stubbing the factory would make them
indistinguishable. The DI seam is
`infrastructure.llm.user_client.set_session_factory`. No PostgreSQL, Kafka
broker, browser, or live provider is required.

Requests go through `httpx.ASGITransport` so the app shares the async
fixture's event loop (SQLAlchemy's async engine is loop-bound, and a
`TestClient` portal would run the app on a different one).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from infrastructure.auth.dependencies import get_current_user_id
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.topics import Topic
from infrastructure.llm import user_client
from infrastructure.security.encryption import ENV_VAR
from jobs.ingestion.api import LLM_NOT_CONFIGURED_MESSAGE, router
from jobs.ingestion.dependencies import (
    get_event_producer,
    get_job_repository,
    get_page_fetcher,
    get_structured_extractor,
)
from shared.types.domain.user import User
from shared.types.domain.user_llm_config import LlmProviderName
from shared.types.ids import UserId
from tests.jobs.conftest import (
    FakeExtractor,
    FakeJobRepository,
    FakePageFetcher,
    make_extracted_fields,
)

pytestmark = pytest.mark.asyncio

API_KEY = "gsk-test-key-not-a-real-credential"
URL = "https://boards.example.com/jobs/42"
PAGE = "posting body"


@dataclass
class Harness:
    app: FastAPI
    client: httpx.AsyncClient
    session_factory: async_sessionmaker[AsyncSession]
    user_id: UserId
    repository: FakeJobRepository
    fetcher: FakePageFetcher
    broker: InMemoryBroker

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
async def harness(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Harness]:
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

    repository = FakeJobRepository()
    fetcher = FakePageFetcher(pages={URL: PAGE})
    extractor = FakeExtractor(by_content={PAGE: make_extracted_fields(company="Acme")})
    broker = InMemoryBroker()
    producer = EventProducer(
        "job-ingestion-service", client=InMemoryProducerClient(broker)
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_job_repository] = lambda: repository
    app.dependency_overrides[get_page_fetcher] = lambda: fetcher
    app.dependency_overrides[get_structured_extractor] = lambda: extractor
    app.dependency_overrides[get_event_producer] = lambda: producer
    app.dependency_overrides[get_current_user_id] = lambda: user_id

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield Harness(
            app=app,
            client=client,
            session_factory=session_factory,
            user_id=user_id,
            repository=repository,
            fetcher=fetcher,
            broker=broker,
        )

    user_client.set_session_factory(None)
    await engine.dispose()


def _assert_gated(response: httpx.Response) -> None:
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "LLM_NOT_CONFIGURED"
    assert detail["message"] == LLM_NOT_CONFIGURED_MESSAGE
    assert detail["message"] == "Add your AI provider key in Settings to use this feature."


async def test_ingest_url_is_gated_when_the_user_has_no_config(harness: Harness) -> None:
    response = await harness.client.post("/jobs/ingest-url", json={"url": URL})

    _assert_gated(response)
    # Gated *before* any work: the posting page was never fetched, no job
    # row was written, and no jobs.discovered event was published.
    assert harness.fetcher.requested == []
    assert harness.repository.jobs == []
    assert harness.broker.log(Topic.JOBS_DISCOVERED.value) == []


async def test_ingest_url_is_gated_when_the_config_was_never_validated(
    harness: Harness,
) -> None:
    """A saved-but-never-validated row means the key behind it has never
    been proven to work, so the submission is blocked exactly as if no row
    existed — the user still has something to fix in Settings.
    """
    await harness.save_llm_config(last_validated_at=None)

    response = await harness.client.post("/jobs/ingest-url", json={"url": URL})

    _assert_gated(response)
    assert harness.fetcher.requested == []
    assert harness.repository.jobs == []
    assert harness.broker.log(Topic.JOBS_DISCOVERED.value) == []


async def test_ingest_url_succeeds_for_a_validly_configured_user(
    harness: Harness,
) -> None:
    await harness.save_llm_config(last_validated_at=datetime.now(UTC))

    response = await harness.client.post("/jobs/ingest-url", json={"url": URL})

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["user_id"] == str(harness.user_id)
    assert body["company"] == "Acme"
    assert body["processing_status"] == "NORMALIZED"
    # Unchanged end-to-end behavior: job persisted and the event published.
    assert len(harness.repository.jobs) == 1
    assert len(harness.broker.log(Topic.JOBS_DISCOVERED.value)) == 1


async def test_unauthenticated_request_still_returns_401_not_the_gate_error(
    harness: Harness,
) -> None:
    """The gate reads the caller's identity from the token the route already
    required, so an unauthenticated request must still fail authentication
    first — this task must not turn a 401 into a 400.
    """
    harness.app.dependency_overrides.pop(get_current_user_id)  # no override -> real dependency

    response = await harness.client.post("/jobs/ingest-url", json={"url": URL})

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


async def test_get_job_route_is_not_gated(harness: Harness) -> None:
    """Only `POST /jobs/ingest-url` is gated; the read route on this router
    keeps working for a user with no key (it is also called
    server-to-server by Outreach Service with no token at all).
    """
    response = await harness.client.get(f"/jobs/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"
