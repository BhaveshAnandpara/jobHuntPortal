"""Tests for `matching.consumers` — the `jobs.discovered` Kafka consumer.

Scenario G (Kafka): a `jobs.discovered` envelope consumed, `correlation_id`
preserved end-to-end into the published `jobs.matched`/`jobs.shortlisted`
envelopes.

Most tests exercise `_handle_job_discovered_async` directly (awaited
in-loop) rather than the outer sync `handle_job_discovered` — the outer
function's only job is to drive the async body via `asyncio.run` (see
`matching.consumers`' module docstring), which cannot itself be called from
inside a already-running event loop (i.e. from inside an
`@pytest.mark.asyncio` test), so it is covered by its own dedicated test
below with the async body replaced by a spy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, String, Table, Uuid, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import matching.consumers as consumers_module
import matching.db as db_module
import matching.events as events_module
import workflows.langgraph.job_matching.nodes as nodes_module
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import build_envelope, deserialize
from infrastructure.kafka.topics import Topic
from matching.errors import MatchingError
from matching.repository import JobMatchRepository
from shared.types.ids import CorrelationId, UserId
from tests.matching.conftest import (
    FakeLLMClient,
    FakeProfileServiceClient,
    FakeUserPreferencesClient,
    make_normalized_job,
    make_resume_profile,
    make_user_preferences,
)
from workflows.langgraph.job_matching.scoring import ProfileScoringOutput

_jobs_table = Table(
    "jobs",
    MetaData(),
    Column("id", Uuid, primary_key=True),
    Column("company", String, nullable=False),
    Column("processing_status", String, nullable=False),
)


async def _insert_job_row(session: AsyncSession, job_id, *, company: str) -> None:
    await session.execute(
        insert(_jobs_table).values(id=job_id, company=company, processing_status="NORMALIZED")
    )
    await session.commit()


def _wire(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    broker: InMemoryBroker,
    profiles_by_user: dict,
    preferences_by_user: dict,
    llm_results: dict | None = None,
    llm_default: object | None = None,
) -> None:
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("job-matching-service", client=InMemoryProducerClient(broker))
    )
    consumers_module.set_user_preferences_client(FakeUserPreferencesClient(preferences_by_user))
    nodes_module.set_profile_service_client(FakeProfileServiceClient(profiles_by_user))
    nodes_module.set_llm_client(FakeLLMClient(llm_results or {}, default=llm_default))


def _strong_score(**overrides: object) -> ProfileScoringOutput:
    fields = {
        "role_relevance": 0.9,
        "skills_fit": 0.9,
        "experience_fit": 0.85,
        "domain_fit": 0.9,
        "seniority_fit": 0.85,
        "location_preference_fit": 0.8,
        "overall_score": 0.88,
        "matched_skills": ["CAD", "GD&T"],
        "missing_skills": [],
        "reasoning": "Strong fit.",
    }
    fields.update(overrides)
    return ProfileScoringOutput(**fields)


@pytest.mark.asyncio
async def test_handle_job_discovered_persists_and_publishes_with_correlation_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    profile = make_resume_profile(user_id, title="Mechanical Design Engineer")
    broker = InMemoryBroker()

    async with session_factory() as session:
        await _insert_job_row(session, job.job_id, company=job.company)

    _wire(
        session_factory,
        broker=broker,
        profiles_by_user={user_id: [profile]},
        preferences_by_user={user_id: make_user_preferences(user_id)},
        llm_results={"Mechanical Design Engineer": _strong_score()},
    )

    envelope = build_envelope(
        Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service", correlation_id=CorrelationId(uuid4())
    )

    await consumers_module._handle_job_discovered_async(envelope)

    matched_messages = broker.log(Topic.JOBS_MATCHED.value)
    shortlisted_messages = broker.log(Topic.JOBS_SHORTLISTED.value)
    assert len(matched_messages) == 1
    assert len(shortlisted_messages) == 1  # 0.88 >= SHORTLIST_THRESHOLD

    matched_envelope = deserialize(Topic.JOBS_MATCHED, matched_messages[0].value())
    assert matched_envelope.correlation_id == envelope.correlation_id
    assert matched_envelope.payload.job_id == job.job_id
    assert matched_envelope.payload.selected_profile_id == profile.profile_id

    shortlisted_envelope = deserialize(Topic.JOBS_SHORTLISTED, shortlisted_messages[0].value())
    assert shortlisted_envelope.correlation_id == envelope.correlation_id

    async with session_factory() as session:
        stored = await JobMatchRepository(session).get_latest_for_job(job.job_id)
        assert stored is not None
        assert stored.selected_resume_id == profile.resume_id

        row = (
            await session.execute(select(_jobs_table).where(_jobs_table.c.id == job.job_id))
        ).one()
        assert row.processing_status == "MATCHED"


@pytest.mark.asyncio
async def test_handle_job_discovered_skips_replayed_message_once_published(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """True-duplicate regression case: row exists AND published_at is
    already set -> pure no-op, no re-scoring, no re-publish. Behavior
    unchanged by the published_at fix (database-ownership.md#job_matches's
    "Publish-reliability column" section, third branch of the three-way
    check).
    """
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    profile = make_resume_profile(user_id, title="Mechanical Design Engineer")
    broker = InMemoryBroker()

    async with session_factory() as session:
        await _insert_job_row(session, job.job_id, company=job.company)

    _wire(
        session_factory,
        broker=broker,
        profiles_by_user={user_id: [profile]},
        preferences_by_user={},
        llm_results={"Mechanical Design Engineer": _strong_score()},
    )

    envelope = build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")

    await consumers_module._handle_job_discovered_async(envelope)
    first_pass_matched = len(broker.log(Topic.JOBS_MATCHED.value))
    assert first_pass_matched == 1

    profile_client = nodes_module._profile_service_client
    llm_client = nodes_module._llm_client

    async with session_factory() as session:
        stored = await JobMatchRepository(session).get_latest_for_job_with_published_at(
            job.job_id
        )
        assert stored is not None
        assert stored[1] is not None  # published_at is set after a clean run

    # Redeliver the identical message.
    await consumers_module._handle_job_discovered_async(envelope)

    assert len(broker.log(Topic.JOBS_MATCHED.value)) == 1  # no duplicate publish
    assert len(broker.log(Topic.JOBS_SHORTLISTED.value)) == 1  # no duplicate publish
    assert profile_client.requested == [user_id]  # not called a second time
    assert len(llm_client.prompts) == 1  # not called a second time


@pytest.mark.asyncio
async def test_handle_job_discovered_no_profiles_available_marks_job_failed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()

    async with session_factory() as session:
        await _insert_job_row(session, job.job_id, company=job.company)

    _wire(
        session_factory,
        broker=broker,
        profiles_by_user={},  # no profiles for this user
        preferences_by_user={},
    )

    envelope = build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")

    await consumers_module._handle_job_discovered_async(envelope)  # must not raise

    assert broker.log(Topic.JOBS_MATCHED.value) == []
    assert broker.log(Topic.JOBS_SHORTLISTED.value) == []

    async with session_factory() as session:
        stored = await JobMatchRepository(session).get_latest_for_job(job.job_id)
        assert stored is None

        row = (
            await session.execute(select(_jobs_table).where(_jobs_table.c.id == job.job_id))
        ).one()
        assert row.processing_status == "FAILED"


@pytest.mark.asyncio
async def test_handle_job_discovered_uses_default_preferences_on_missing_user_preferences(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """User Service 404 (no UserPreferences configured) must not fail the
    match — matching.consumers._empty_preferences fills in a neutral
    default (see that function's docstring)."""
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    profile = make_resume_profile(user_id, title="Mechanical Design Engineer")
    broker = InMemoryBroker()

    async with session_factory() as session:
        await _insert_job_row(session, job.job_id, company=job.company)

    _wire(
        session_factory,
        broker=broker,
        profiles_by_user={user_id: [profile]},
        preferences_by_user={},  # FakeUserPreferencesClient returns None -> 404 equivalent
        llm_results={"Mechanical Design Engineer": _strong_score()},
    )

    envelope = build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")

    await consumers_module._handle_job_discovered_async(envelope)

    assert len(broker.log(Topic.JOBS_MATCHED.value)) == 1


@pytest.mark.asyncio
async def test_handle_job_discovered_raises_matching_error_on_publish_failure(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    profile = make_resume_profile(user_id, title="Mechanical Design Engineer")

    async with session_factory() as session:
        await _insert_job_row(session, job.job_id, company=job.company)

    class BrokenProducer:
        def publish(self, *args, **kwargs):
            raise RuntimeError("kafka broker unreachable")

    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(BrokenProducer())
    consumers_module.set_user_preferences_client(
        FakeUserPreferencesClient({user_id: make_user_preferences(user_id)})
    )
    nodes_module.set_profile_service_client(FakeProfileServiceClient({user_id: [profile]}))
    nodes_module.set_llm_client(
        FakeLLMClient({"Mechanical Design Engineer": _strong_score()})
    )

    envelope = build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")

    with pytest.raises(MatchingError):
        await consumers_module._handle_job_discovered_async(envelope)

    # The JobMatch row is already committed by the time the publish step
    # fails, left with published_at still NULL — exactly the state the
    # three-way idempotency check's second branch (below) knows how to
    # resume from, closing what used to be a "known limitation" (a
    # persisted-but-never-published job silently skipped forever).
    async with session_factory() as session:
        stored = await JobMatchRepository(session).get_latest_for_job_with_published_at(
            job.job_id
        )
        assert stored is not None
        _job_match, published_at = stored
        assert published_at is None


@pytest.mark.asyncio
async def test_handle_job_discovered_republishes_without_rescoring_when_persisted_but_unpublished(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The scenario this fix closes: persist succeeds, publish raises (as
    in the test above), then a second `handle_job_discovered` call for the
    same job_id (simulating Kafka redelivery or in-band retry) must: skip
    re-scoring entirely, successfully republish jobs.matched (+
    jobs.shortlisted), and mark published_at.
    """
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    profile = make_resume_profile(user_id, title="Mechanical Design Engineer")

    async with session_factory() as session:
        await _insert_job_row(session, job.job_id, company=job.company)

    class BrokenProducer:
        def publish(self, *args, **kwargs):
            raise RuntimeError("kafka broker unreachable")

    profile_client = FakeProfileServiceClient({user_id: [profile]})
    llm_client = FakeLLMClient({"Mechanical Design Engineer": _strong_score()})

    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(BrokenProducer())
    consumers_module.set_user_preferences_client(
        FakeUserPreferencesClient({user_id: make_user_preferences(user_id)})
    )
    nodes_module.set_profile_service_client(profile_client)
    nodes_module.set_llm_client(llm_client)

    envelope = build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")

    # First delivery: persist succeeds, publish fails.
    with pytest.raises(MatchingError):
        await consumers_module._handle_job_discovered_async(envelope)

    assert len(profile_client.requested) == 1
    assert len(llm_client.prompts) == 1

    # Redelivery, now with a working producer — must not re-score.
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("job-matching-service", client=InMemoryProducerClient(broker))
    )

    await consumers_module._handle_job_discovered_async(envelope)

    assert len(profile_client.requested) == 1  # not called again
    assert len(llm_client.prompts) == 1  # not called again

    matched_messages = broker.log(Topic.JOBS_MATCHED.value)
    shortlisted_messages = broker.log(Topic.JOBS_SHORTLISTED.value)
    assert len(matched_messages) == 1
    assert len(shortlisted_messages) == 1  # 0.88 >= SHORTLIST_THRESHOLD

    matched_envelope = deserialize(Topic.JOBS_MATCHED, matched_messages[0].value())
    assert matched_envelope.payload.job_id == job.job_id
    assert matched_envelope.correlation_id == envelope.correlation_id

    async with session_factory() as session:
        stored = await JobMatchRepository(session).get_latest_for_job_with_published_at(
            job.job_id
        )
        assert stored is not None
        _job_match, published_at = stored
        assert published_at is not None


def test_handle_job_discovered_sync_wrapper_drives_the_async_body(monkeypatch) -> None:
    """The outer `handle_job_discovered` must be a plain sync function
    (matching `EventConsumer`'s `EventHandler` contract) that actually
    executes `_handle_job_discovered_async` via `asyncio.run` — verified
    here with the async body replaced by a spy, so this test needs no real
    event loop/DB machinery of its own.
    """
    calls: list[object] = []

    async def fake_async_handler(envelope: object) -> None:
        calls.append(envelope)

    monkeypatch.setattr(consumers_module, "_handle_job_discovered_async", fake_async_handler)

    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    envelope = build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")

    consumers_module.handle_job_discovered(envelope)

    assert calls == [envelope]


@pytest.mark.asyncio
async def test_handle_profile_updated_is_deferred() -> None:
    from shared.events.payloads import ProfileUpdateSummary
    from shared.types.enums import ProfileChangeType
    from shared.types.ids import ProfileId, ResumeId

    payload = ProfileUpdateSummary(
        profile_id=ProfileId(uuid4()),
        user_id=UserId(uuid4()),
        resume_id=ResumeId(uuid4()),
        change_type=ProfileChangeType.CREATED,
        updated_at=datetime.now(UTC),
    )
    envelope = build_envelope(Topic.PROFILES_UPDATED, payload, producer="profile-service")

    with pytest.raises(NotImplementedError):
        await consumers_module.handle_profile_updated(envelope)
