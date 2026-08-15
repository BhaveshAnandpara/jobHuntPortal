"""Shared integration-test harness.

Builds exactly one shared SQLite (async) database, one shared in-memory
Kafka broker, and one combined FastAPI app (`api.main.app`) with every
component's DB/producer/LLM/external-client DI seam pointed at them — per
this suite's brief (see `.claude/agents/integration-agent.md` and the task
prompt's "Building one shared test harness" section).

Reuses each component's own established test patterns rather than
reinventing them:
  - `infrastructure.kafka.in_memory.InMemoryBroker` / `InMemoryProducerClient`
    (see `tests/matching/test_events.py`, `tests/contacts/test_consumers.py`).
  - Each component's own `Fake*` test doubles from `tests/<component>/conftest.py`.
  - Direct `_handle_x_async(envelope)` invocation instead of the sync
    `EventConsumer` wrapper (see `tests/matching/test_consumers.py`).

Two categories of dependency override are needed per component:
  - Kafka *consumer*-side DB/producer access -> each component's own
    `<component>.db.set_session_factory` / `<component>.events.set_event_producer`.
  - API-layer DB access -> FastAPI `app.dependency_overrides[...]`. For every
    component except Resume/Profile Service, overriding just the low-level
    `get_session`/`get_db_session` dependency is sufficient — FastAPI resolves
    nested `Depends(...)` chains by identity, so a route's own
    `Depends(get_job_repository)` (which itself does
    `Depends(get_db_session)`) automatically picks up the override with no
    need to also override `get_job_repository`. Resume/Profile Service is the
    one exception: `profiles.api.dependencies.get_profile_service` builds its
    producer/storage/llm_client via *plain function calls*, not `Depends(...)`,
    so overriding those sub-functions individually would have no effect —
    only overriding `get_profile_service` itself works (matching
    `tests/profiles/test_api.py`'s own established technique).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import pytest_asyncio
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Consumer-boundary / producer-boundary / DI-seam modules for every
# component with a Kafka consumer and/or LangGraph workflow.
# ---------------------------------------------------------------------------
import contacts.consumers as contacts_consumers
import contacts.db as contacts_db
import contacts.events as contacts_events

# ---------------------------------------------------------------------------
# Import every component's models.py so its tables register on the shared
# Base.metadata (see infrastructure.database.Base's docstring).
# ---------------------------------------------------------------------------
import contacts.models  # noqa: F401
import jobs.models  # noqa: F401
import matching.consumers as matching_consumers
import matching.db as matching_db
import matching.events as matching_events
import matching.models  # noqa: F401
import outreach.consumers as outreach_consumers
import outreach.db as outreach_db
import outreach.events as outreach_events
import outreach.models  # noqa: F401
import profiles.models  # noqa: F401
import tracking.consumers as tracking_consumers
import tracking.db as tracking_db
import tracking.events as tracking_events
import tracking.models  # noqa: F401
import users.models  # noqa: F401
import workflows.langgraph.contact_discovery.nodes as contact_nodes
import workflows.langgraph.job_matching.nodes as matching_nodes
import workflows.langgraph.outreach_generation.nodes as outreach_nodes
from api.main import app as fastapi_app
from contacts.api.dependencies import get_session as contacts_get_session
from infrastructure.database import Base
from infrastructure.database.session import session_scope
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize
from infrastructure.kafka.topics import Topic
from jobs.ingestion.dependencies import get_db_session as jobs_get_db_session
from jobs.ingestion.dependencies import get_event_producer as jobs_get_event_producer
from jobs.ingestion.dependencies import get_page_fetcher as jobs_get_page_fetcher
from jobs.ingestion.dependencies import (
    get_structured_extractor as jobs_get_structured_extractor,
)
from matching.api.dependencies import get_session as matching_get_session
from outreach.api.dependencies import get_session as outreach_get_session
from profiles.api.dependencies import (
    get_profile_service as profiles_get_profile_service,
)
from profiles.api.dependencies import get_session as profiles_get_session
from profiles.repository import CandidateProfileRepository, ResumeRepository
from profiles.service import ProfileService
from profiles.storage import LocalResumeStorage
from shared.events.envelope import EventEnvelope

# Re-exported fakes/factories from each component's own test suite — reused
# per the task brief ("do not write new fakes when a compatible one already
# exists"), aliased where names collide across components.
from tests.contacts.conftest import FakeLLMClient as ContactsFakeLLMClient
from tests.contacts.conftest import (
    FakePeopleSearchClient,
    make_hit,
    make_search_request,
)
from tests.jobs.conftest import FakeExtractor, FakePageFetcher, make_extracted_fields
from tests.matching.conftest import FakeLLMClient as MatchingFakeLLMClient
from tests.matching.conftest import (
    FakeProfileServiceClient as MatchingFakeProfileServiceClient,
)
from tests.matching.conftest import FakeUserPreferencesClient
from tests.outreach.conftest import FakeJobIngestionClient, FakeJobMatchClient
from tests.outreach.conftest import FakeLLMClient as OutreachFakeLLMClient
from tests.outreach.conftest import (
    FakeProfileServiceClient as OutreachFakeProfileServiceClient,
)
from tests.profiles.conftest import ScriptedLLMProvider, llm_response, make_llm_client
from tracking.api.dependencies import get_session as tracking_get_session
from users.api.dependencies import get_session as users_get_session

# ---------------------------------------------------------------------------
# The documented fan-out map (per the task prompt) — which consumer(s) fire
# on which topic. profiles.updated is intentionally absent: Job Matching
# Service's handle_profile_updated is a deferred stub, not wired anywhere.
# ---------------------------------------------------------------------------

FAN_OUT: dict[Topic, list[Callable[[EventEnvelope], Awaitable[None]]]] = {
    Topic.JOBS_DISCOVERED: [
        matching_consumers._handle_job_discovered_async,
        tracking_consumers._handle_job_discovered_async,
    ],
    Topic.JOBS_MATCHED: [tracking_consumers._handle_job_matched_async],
    Topic.JOBS_SHORTLISTED: [tracking_consumers._handle_job_shortlisted_async],
    Topic.CONTACTS_REQUESTED: [contacts_consumers._handle_contacts_requested_async],
    Topic.CONTACTS_FOUND: [
        outreach_consumers._handle_contacts_found_async,
        tracking_consumers._handle_contacts_found_async,
    ],
    Topic.OUTREACH_GENERATED: [tracking_consumers._handle_outreach_generated_async],
    Topic.OUTREACH_APPROVED: [
        outreach_consumers._handle_outreach_approved_async,
        tracking_consumers._handle_outreach_approved_async,
    ],
    Topic.OUTREACH_SENT: [tracking_consumers._handle_outreach_sent_async],
}

# Primary-chain order used by drain_chain below. outreach.approved is
# deliberately never auto-produced by drain_chain — it is only ever
# published from POST /outreach/{id}/approve (a real causal-chain origin,
# see event-contracts.md's correlation_id note) — callers must approve via
# the API, then call drain_chain again to pick it up.
CHAIN_ORDER: list[Topic] = [
    Topic.JOBS_DISCOVERED,
    Topic.JOBS_MATCHED,
    Topic.JOBS_SHORTLISTED,
    Topic.CONTACTS_REQUESTED,
    Topic.CONTACTS_FOUND,
    Topic.OUTREACH_GENERATED,
    Topic.OUTREACH_APPROVED,
    Topic.OUTREACH_SENT,
]


def log_envelopes(broker: InMemoryBroker, topic: Topic) -> list[EventEnvelope]:
    """Every message ever published to `topic`, deserialized in order."""
    return [deserialize(topic, message.value()) for message in broker.log(topic.value)]


async def dispatch(topic: Topic, envelope: EventEnvelope) -> None:
    """Invoke every consumer documented to fire on `topic`, in the fan-out
    map's order, for one already-deserialized envelope.
    """
    for handler in FAN_OUT[topic]:
        await handler(envelope)


async def drain_chain(
    broker: InMemoryBroker, *, offsets: dict[Topic, int] | None = None
) -> dict[Topic, int]:
    """Dispatch every not-yet-delivered message on every topic in
    `CHAIN_ORDER`, following the fan-out map, until no topic has new
    messages (a single pass may produce new messages on a later topic,
    which a later pass then picks up). Returns per-topic consumed offsets
    so a caller can resume (e.g. after POSTing an approval) by passing them
    back in via `offsets=`.
    """
    offsets = dict(offsets or {})
    changed = True
    while changed:
        changed = False
        for topic in CHAIN_ORDER:
            log = broker.log(topic.value)
            start = offsets.get(topic, 0)
            if start >= len(log):
                continue
            for message in log[start:]:
                envelope = deserialize(topic, message.value())
                await dispatch(topic, envelope)
                changed = True
            offsets[topic] = len(log)
    return offsets


def snapshot_offsets(broker: InMemoryBroker) -> dict[Topic, int]:
    """Current message count per chain topic, suitable as `drain_chain`'s
    `offsets=` argument so a subsequent call doesn't redispatch messages a
    caller already processed manually (e.g. the phase-1 hand-driven part of
    the primary flow test, before `sync_outreach_clients` has anything to
    sync from).
    """
    return {topic: len(broker.log(topic.value)) for topic in CHAIN_ORDER}


# ---------------------------------------------------------------------------
# Session-dependency override factory — shared shape every component's own
# get_session/get_db_session already uses (see infrastructure.database.session
# .get_session's docstring): commit on success, rollback on error, always
# close.
# ---------------------------------------------------------------------------


def _session_dependency_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], AsyncIterator[AsyncSession]]:
    async def _dependency() -> AsyncIterator[AsyncSession]:
        session = session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    return _dependency


# ---------------------------------------------------------------------------
# The harness object every integration test receives.
# ---------------------------------------------------------------------------


@dataclass
class IntegrationHarness:
    app: object
    client: TestClient
    broker: InMemoryBroker
    session_factory: async_sessionmaker[AsyncSession]
    tmp_path: Path
    _llm_holder: dict[str, object] = field(default_factory=dict)

    # -- Users / Profiles convenience -------------------------------------------------

    def create_user(
        self, *, email: str | None = None, display_name: str = "Test User", password: str = "test-password-123"
    ):
        """Returns the flat user dict (`id`, `email`, ...) plus
        `access_token`, so existing callers reading `user["id"]` are
        unaffected while callers that need to act as this user (once a
        service's routes require a bearer token) can use
        `user["access_token"]`."""
        email = email or f"{uuid4()}@example.com"
        response = self.client.post(
            "/users", json={"email": email, "display_name": display_name, "password": password}
        )
        assert response.status_code == 201, response.text
        body = response.json()
        return {**body["user"], "access_token": body["access_token"]}

    def auth_headers(self, user: dict) -> dict[str, str]:
        return {"Authorization": f"Bearer {user['access_token']}"}

    def set_profiles_llm(self, script: list) -> ScriptedLLMProvider:
        """Wire Resume/Profile Service's parsing LLM to a fresh
        `ScriptedLLMProvider` seeded with `script` (a list of `LLMResponse`/
        `Exception`, popped in upload order — see
        `tests/profiles/conftest.py:make_llm_client`). Returns the provider
        so a test can assert on `.requests` (e.g. profession-independence).
        """
        llm_client, provider = make_llm_client(script)
        self._llm_holder["client"] = llm_client
        return provider

    def upload_resume(self, user: dict, file_name: str, text: str) -> dict:
        import base64

        response = self.client.post(
            "/resumes",
            json={
                "file_name": file_name,
                "file_content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            },
            headers=self.auth_headers(user),
        )
        assert response.status_code == 202, response.text
        return response.json()

    def list_profiles(self, user: dict) -> list[dict]:
        response = self.client.get("/profiles", headers=self.auth_headers(user))
        assert response.status_code == 200, response.text
        return response.json()

    # -- Job ingestion convenience ------------------------------------------------

    def set_job_ingestion_fakes(
        self,
        *,
        pages: dict[str, str] | None = None,
        extractor_by_content: dict | None = None,
        extractor_default=None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        fetcher = FakePageFetcher(pages=pages or {}, errors=errors or {})
        extractor = FakeExtractor(
            by_content=extractor_by_content or {}, default=extractor_default
        )
        self.app.dependency_overrides[jobs_get_page_fetcher] = lambda: fetcher
        self.app.dependency_overrides[jobs_get_structured_extractor] = lambda: extractor

    def ingest_job(self, user: dict, url: str) -> dict:
        """`user` is the dict `create_user()` returns (needs `access_token`,
        not just `id` — identity is token-derived now, not a request field).
        """
        response = self.client.post(
            "/jobs/ingest-url", json={"url": url}, headers=self.auth_headers(user)
        )
        assert response.status_code == 202, response.text
        return response.json()

    # -- Matching workflow fakes ----------------------------------------------

    def sync_matching_profiles(self, user: dict) -> MatchingFakeProfileServiceClient:
        """Build a `FakeProfileServiceClient` from the *real* `GET /profiles`
        API response for `user`, and wire it into
        `workflows.langgraph.job_matching.nodes`. Keeps the matching
        workflow's profile input consistent with what Resume/Profile
        Service's real API+DB actually produced, per this component's own
        documented DI seam (see this module's docstring).
        """
        from uuid import UUID

        from shared.types.dto import ResumeProfile
        from shared.types.ids import UserId

        profiles = [ResumeProfile(**item) for item in self.list_profiles(user)]
        fake = MatchingFakeProfileServiceClient({UserId(UUID(user["id"])): profiles})
        matching_nodes.set_profile_service_client(fake)
        return fake

    def set_matching_llm(self, llm: MatchingFakeLLMClient) -> None:
        matching_nodes.set_llm_client(llm)

    def set_matching_preferences(self, preferences: FakeUserPreferencesClient) -> None:
        matching_consumers.set_user_preferences_client(preferences)

    # -- Contact discovery fakes ------------------------------------------------

    def set_contacts_fakes(
        self, *, hits: list | None = None, llm: ContactsFakeLLMClient | None = None
    ) -> None:
        contact_nodes.set_people_search_client(FakePeopleSearchClient(hits=hits or []))
        if llm is not None:
            contact_nodes.set_llm_client(llm)

    # -- Outreach generation fakes -----------------------------------------------

    def sync_outreach_clients(self, job_id: str) -> None:
        """Build `FakeJobMatchClient`/`FakeProfileServiceClient`/
        `FakeJobIngestionClient` from the *real* `GET /jobs/{id}/matches`,
        `GET /profiles/{id}`, and `GET /jobs/{id}` API responses, and wire
        them into `outreach.consumers` — same "sync fakes from the real API"
        technique as `sync_matching_profiles`.
        """
        from uuid import UUID

        from shared.types.api.matching import JobMatchResponse
        from shared.types.dto import ResumeProfile
        from shared.types.ids import JobId

        match_response = self.client.get(f"/jobs/{job_id}/matches")
        assert match_response.status_code == 200, match_response.text
        job_match = JobMatchResponse(**match_response.json())

        job_response = self.client.get(f"/jobs/{job_id}")
        assert job_response.status_code == 200, job_response.text

        profile_response = self.client.get(f"/profiles/{job_match.selected_profile_id}")
        assert profile_response.status_code == 200, profile_response.text
        profile = ResumeProfile(**profile_response.json())

        job_id_key = JobId(UUID(job_id))
        outreach_consumers.set_job_match_client(
            FakeJobMatchClient({job_id_key: job_match})
        )
        outreach_consumers.set_job_ingestion_client(
            FakeJobIngestionClient({job_id_key: _job_response(job_response.json())})
        )
        outreach_consumers.set_profile_service_client(
            OutreachFakeProfileServiceClient({job_match.selected_profile_id: profile})
        )

    def set_outreach_llm(self, llm: OutreachFakeLLMClient) -> None:
        outreach_nodes.set_llm_client(llm)

    # -- Approval convenience -------------------------------------------------

    def approve_outreach(self, outreach_id: str, *, final_message: str | None = None) -> dict:
        response = self.client.post(
            f"/outreach/{outreach_id}/approve", json={"final_message": final_message}
        )
        assert response.status_code == 200, response.text
        return response.json()

    def reject_outreach(self, outreach_id: str) -> dict:
        response = self.client.post(f"/outreach/{outreach_id}/reject")
        assert response.status_code == 200, response.text
        return response.json()


def _job_response(data: dict):
    from shared.types.api.jobs import JobResponse

    return JobResponse(**data)


# ---------------------------------------------------------------------------
# The fixture.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> AsyncIterator[IntegrationHarness]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    broker = InMemoryBroker()

    matching_producer = EventProducer(
        matching_events.PRODUCER_NAME, client=InMemoryProducerClient(broker)
    )
    contacts_producer = EventProducer(
        contacts_events.PRODUCER_NAME, client=InMemoryProducerClient(broker)
    )
    outreach_producer = EventProducer(
        outreach_events.PRODUCER_NAME, client=InMemoryProducerClient(broker)
    )
    tracking_producer = EventProducer(
        tracking_events.PRODUCER_NAME, client=InMemoryProducerClient(broker)
    )
    jobs_producer = EventProducer(
        "job-ingestion-service", client=InMemoryProducerClient(broker)
    )
    profiles_producer = EventProducer(
        "resume-profile-service", client=InMemoryProducerClient(broker)
    )

    matching_events.set_event_producer(matching_producer)
    contacts_events.set_event_producer(contacts_producer)
    outreach_events.set_event_producer(outreach_producer)
    tracking_events.set_event_producer(tracking_producer)

    matching_db.set_session_factory(session_factory)
    contacts_db.set_session_factory(session_factory)
    outreach_db.set_session_factory(session_factory)
    tracking_db.set_session_factory(session_factory)

    app = fastapi_app
    app.dependency_overrides = {}

    app.dependency_overrides[users_get_session] = _session_dependency_factory(session_factory)
    app.dependency_overrides[jobs_get_db_session] = _session_dependency_factory(session_factory)
    app.dependency_overrides[matching_get_session] = _session_dependency_factory(session_factory)
    app.dependency_overrides[contacts_get_session] = _session_dependency_factory(session_factory)
    app.dependency_overrides[outreach_get_session] = _session_dependency_factory(session_factory)
    app.dependency_overrides[tracking_get_session] = _session_dependency_factory(session_factory)
    app.dependency_overrides[profiles_get_session] = _session_dependency_factory(session_factory)
    app.dependency_overrides[jobs_get_event_producer] = lambda: jobs_producer

    resume_storage = LocalResumeStorage(base_dir=tmp_path / "resumes")
    llm_holder: dict[str, object] = {"client": None}

    def _profile_service_override(
        session: Annotated[AsyncSession, Depends(profiles_get_session)],
    ) -> ProfileService:
        return ProfileService(
            ResumeRepository(session),
            CandidateProfileRepository(session),
            producer=profiles_producer,
            storage=resume_storage,
            llm_client=llm_holder["client"],
        )

    app.dependency_overrides[profiles_get_profile_service] = _profile_service_override

    client = TestClient(app)
    harness = IntegrationHarness(
        app=app,
        client=client,
        broker=broker,
        session_factory=session_factory,
        tmp_path=tmp_path,
        _llm_holder=llm_holder,
    )

    try:
        yield harness
    finally:
        client.close()
        app.dependency_overrides = {}

        matching_nodes.set_profile_service_client(None)
        matching_nodes.set_llm_client(None)
        matching_consumers.set_graph(None)
        matching_consumers.set_user_preferences_client(None)
        matching_db.set_session_factory(None)
        matching_events.set_event_producer(None)

        contact_nodes.set_people_search_client(None)
        contact_nodes.set_llm_client(None)
        contacts_consumers.set_graph(None)
        contacts_db.set_session_factory(None)
        contacts_events.set_event_producer(None)

        outreach_nodes.set_llm_client(None)
        outreach_consumers.set_graph(None)
        outreach_consumers.set_job_match_client(None)
        outreach_consumers.set_profile_service_client(None)
        outreach_consumers.set_job_ingestion_client(None)
        outreach_consumers.set_message_send_client(None)
        outreach_db.set_session_factory(None)
        outreach_events.set_event_producer(None)

        tracking_db.set_session_factory(None)
        tracking_events.set_event_producer(None)

        await engine.dispose()


__all__ = [
    "CHAIN_ORDER",
    "FAN_OUT",
    "ContactsFakeLLMClient",
    "FakeExtractor",
    "FakeJobIngestionClient",
    "FakeJobMatchClient",
    "FakePageFetcher",
    "FakePeopleSearchClient",
    "FakeUserPreferencesClient",
    "IntegrationHarness",
    "MatchingFakeLLMClient",
    "MatchingFakeProfileServiceClient",
    "OutreachFakeLLMClient",
    "OutreachFakeProfileServiceClient",
    "dispatch",
    "drain_chain",
    "harness",
    "llm_response",
    "log_envelopes",
    "make_extracted_fields",
    "make_hit",
    "make_search_request",
    "session_scope",
    "snapshot_offsets",
]
