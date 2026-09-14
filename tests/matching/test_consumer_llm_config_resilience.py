"""One user's missing AI provider key must not affect anybody else's
opportunity, and must never take the shared consumer thread down with it
(docs/llm-provider-config/TECH.md Task 8).

Everything here runs through the **real `EventConsumer.run()` loop** rather
than calling `_handle_job_discovered_async` directly, which is the whole
point: the claim under test is about the consumer, not about one handler
call in isolation. `EventConsumer` polls a real `InMemoryBroker`, and
`handle_job_discovered` drives its own `asyncio.run` per message exactly as
it does under `scripts/run_consumers.py`.

Two users, two jobs, one consumer, in sequence:

  * user A has no validated `user_llm_config` -> `get_user_llm_client`
    raises `LlmNotConfiguredError` inside the scoring node
  * user B has one -> the workflow scores, persists and publishes normally

Asserted: A's `Application` lands in `BLOCKED_LLM_NOT_CONFIGURED` with an
`application_history` row for the transition, B's opportunity completes
untouched, nothing is dead-lettered, and `run()` returns normally rather
than propagating A's exception.

The blocked write reaches Tracking Service through
`shared.contracts.opportunity_blocked`'s port — Job Matching may not import
`tracking` (enforced by `tests/integration/test_architecture_audit.py`), so
this test wires the port the same way `scripts/run_consumers.py` does.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, String, Table, Uuid, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import matching.consumers as consumers_module
import matching.db as matching_db
import matching.events as matching_events
import tracking.consumers as tracking_consumers
import tracking.db as tracking_db
import tracking.events as tracking_events
import workflows.langgraph.job_matching.nodes as nodes_module
from infrastructure.database import Base
from infrastructure.kafka.consumer import EventConsumer
from infrastructure.kafka.in_memory import (
    InMemoryBroker,
    InMemoryConsumerClient,
    InMemoryProducerClient,
)
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.topics import Topic, dlq_topic
from infrastructure.llm import LlmNotConfiguredError
from shared.contracts.opportunity_blocked import set_opportunity_blocked_reporter
from shared.types.enums import ApplicationStatus
from shared.types.ids import UserId
from tests.matching.conftest import (
    FakeLLMClient,
    FakeProfileServiceClient,
    FakeUserPreferencesClient,
    make_normalized_job,
    make_resume_profile,
    make_user_preferences,
)
from tracking.repository import ApplicationHistoryRepository, ApplicationRepository
from workflows.langgraph.job_matching.scoring import ProfileScoringOutput

_jobs_stand_in = MetaData()
Table(
    "jobs",
    _jobs_stand_in,
    Column("id", Uuid, primary_key=True),
    Column("company", String, nullable=False),
    Column("processing_status", String, nullable=False),
)


def _strong_score() -> ProfileScoringOutput:
    return ProfileScoringOutput(
        role_relevance=0.9,
        skills_fit=0.9,
        experience_fit=0.85,
        domain_fit=0.9,
        seniority_fit=0.85,
        location_preference_fit=0.8,
        overall_score=0.88,
        matched_skills=["CAD", "GD&T"],
        missing_skills=[],
        reasoning="Strong fit.",
    )


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator[async_sessionmaker[AsyncSession]]:
    """A file-backed SQLite DB carrying both components' tables.

    File-backed with `NullPool`, not `:memory:`, on purpose: the consumer
    under test calls `asyncio.run` once per message, so each message runs on
    a *fresh event loop*. A pooled async connection created on one loop
    cannot be reused on the next; `NullPool` opens a new connection per
    checkout, and a real file keeps the data across those connections.
    """
    pytest.importorskip(
        "aiosqlite", reason="async sqlite driver not installed; DB tests need it"
    )
    from matching.models import JobMatchRecord
    from tracking.models import ApplicationHistoryRecord, ApplicationRecord

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'resilience.db'}", poolclass=NullPool
    )

    async def _create() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[
                    JobMatchRecord.__table__,
                    ApplicationRecord.__table__,
                    ApplicationHistoryRecord.__table__,
                ],
            )
            await connection.run_sync(_jobs_stand_in.create_all)

    asyncio.run(_create())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    asyncio.run(engine.dispose())


@pytest.fixture
def blocked_reporter_wired() -> Iterator[None]:
    """Wire Tracking Service into the blocked-opportunity port, exactly as
    `scripts/run_consumers.py` does at startup, and unwire afterwards."""
    set_opportunity_blocked_reporter(tracking_consumers.record_opportunity_blocked)
    yield
    set_opportunity_blocked_reporter(None)


@pytest.fixture
def tracking_wiring_reset() -> Iterator[None]:
    """`tests/matching/conftest.py`'s autouse reset only covers Job
    Matching's own seams; this test also drives Tracking Service."""
    yield
    tracking_db.set_session_factory(None)
    tracking_events.set_event_producer(None)


def test_missing_llm_config_blocks_only_that_users_opportunity(
    session_factory: async_sessionmaker[AsyncSession],
    blocked_reporter_wired: None,
    tracking_wiring_reset: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_without_key = UserId(uuid4())
    user_with_key = UserId(uuid4())
    blocked_job = make_normalized_job(user_without_key, company="Acme Robotics")
    healthy_job = make_normalized_job(user_with_key, company="Vector Dynamics")

    broker = InMemoryBroker()
    matching_db.set_session_factory(session_factory)
    tracking_db.set_session_factory(session_factory)
    matching_events.set_event_producer(
        EventProducer("job-matching-service", client=InMemoryProducerClient(broker))
    )
    tracking_events.set_event_producer(
        EventProducer("tracking-service", client=InMemoryProducerClient(broker))
    )

    profile_a = make_resume_profile(user_without_key, title="Blocked User Profile")
    profile_b = make_resume_profile(user_with_key, title="Funded User Profile")
    consumers_module.set_user_preferences_client(
        FakeUserPreferencesClient(
            {
                user_without_key: make_user_preferences(user_without_key),
                user_with_key: make_user_preferences(user_with_key),
            }
        )
    )
    nodes_module.set_profile_service_client(
        FakeProfileServiceClient(
            {user_without_key: [profile_a], user_with_key: [profile_b]}
        )
    )

    # Per-user LLM resolution — deliberately *not* `set_llm_client`, which
    # overrides every user at once and so could not express "one configured
    # user, one unconfigured user", the entire scenario under test.
    working_client = FakeLLMClient({"Funded User Profile": _strong_score()})

    async def fake_get_user_llm_client(user_id: UserId):
        if user_id == user_without_key:
            raise LlmNotConfiguredError(user_id)
        return working_client

    monkeypatch.setattr(nodes_module, "get_user_llm_client", fake_get_user_llm_client)

    async def _seed_jobs() -> None:
        async with session_factory() as session:
            for job in (blocked_job, healthy_job):
                await session.execute(
                    insert(_jobs_stand_in.tables["jobs"]).values(
                        id=job.job_id,
                        company=job.company,
                        processing_status="NORMALIZED",
                    )
                )
            await session.commit()

    asyncio.run(_seed_jobs())

    # Both jobs enter the pipeline on jobs.discovered, blocked user first.
    ingestion = EventProducer("job-ingestion-service", client=InMemoryProducerClient(broker))
    ingestion.publish(Topic.JOBS_DISCOVERED, blocked_job)
    ingestion.publish(Topic.JOBS_DISCOVERED, healthy_job)

    # Tracking Service's own jobs.discovered consumer group runs first, as it
    # does in production, so both opportunities already exist at DISCOVERED
    # and the blocked one is a real transition rather than a fresh insert.
    tracking_consumer = EventConsumer(
        Topic.JOBS_DISCOVERED,
        "tracking-service-jobs-discovered",
        tracking_consumers.handle_job_discovered,
        client=InMemoryConsumerClient(broker, "tracking-service-jobs-discovered"),
        dlq_producer=ingestion,
        sleep=lambda _seconds: None,
    )
    assert tracking_consumer.run(max_messages=2) == 2

    matching_consumer = EventConsumer(
        Topic.JOBS_DISCOVERED,
        "job-matching-service",
        consumers_module.handle_job_discovered,
        client=InMemoryConsumerClient(broker, "job-matching-service"),
        dlq_producer=ingestion,
        sleep=lambda _seconds: None,
    )

    # The assertion that matters most: this call returns normally. Before
    # this task, user A's LlmNotConfiguredError was normalized into a
    # MatchingError and retried/dead-lettered instead.
    processed = matching_consumer.run(max_messages=2)
    assert processed == 2

    # ...and nothing was dead-lettered on the way.
    assert broker.log(dlq_topic(Topic.JOBS_DISCOVERED)) == []

    async def _read_state() -> tuple:
        async with session_factory() as session:
            app_repo = ApplicationRepository(session)
            history_repo = ApplicationHistoryRepository(session)
            blocked_app = await app_repo.get_for_job(blocked_job.job_id)
            healthy_app = await app_repo.get_for_job(healthy_job.job_id)
            blocked_history = await history_repo.list_for_application(blocked_app.id)
            job_match_ids = (
                await session.execute(
                    select(Base.metadata.tables["job_matches"].c.job_id)
                )
            ).scalars().all()
            return blocked_app, healthy_app, blocked_history, set(job_match_ids)

    blocked_app, healthy_app, blocked_history, matched_job_ids = asyncio.run(_read_state())

    # User A: paused, with a user-readable reason and a recorded transition.
    assert blocked_app.status == ApplicationStatus.BLOCKED_LLM_NOT_CONFIGURED
    assert blocked_app.notes == tracking_consumers.BLOCKED_LLM_NOTE
    assert [entry.to_status for entry in blocked_history] == [
        ApplicationStatus.DISCOVERED,
        ApplicationStatus.BLOCKED_LLM_NOT_CONFIGURED,
    ]
    assert blocked_history[-1].from_status == ApplicationStatus.DISCOVERED
    assert blocked_history[-1].triggered_by == "job-matching-service"

    # User B: entirely unaffected — matched, persisted, and published.
    assert healthy_app.status == ApplicationStatus.DISCOVERED  # tracking hasn't consumed jobs.matched here
    assert matched_job_ids == {healthy_job.job_id}
    matched = broker.log(Topic.JOBS_MATCHED.value)
    assert len(matched) == 1

    # And no JobMatch was invented for the blocked user.
    assert blocked_job.job_id not in matched_job_ids


def test_blocked_report_is_idempotent_across_redelivery(
    session_factory: async_sessionmaker[AsyncSession],
    blocked_reporter_wired: None,
    tracking_wiring_reset: None,
) -> None:
    """A redelivered message must not append a second history row — the same
    at-least-once tolerance every other Tracking transition has."""
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)

    broker = InMemoryBroker()
    tracking_db.set_session_factory(session_factory)
    tracking_events.set_event_producer(
        EventProducer("tracking-service", client=InMemoryProducerClient(broker))
    )

    async def _report_twice() -> tuple:
        from shared.contracts.opportunity_blocked import (
            OpportunityBlockedReason,
            report_opportunity_blocked,
        )

        for _ in range(2):
            await report_opportunity_blocked(
                job_id=job.job_id,
                user_id=user_id,
                reason=OpportunityBlockedReason.LLM_NOT_CONFIGURED,
                reported_by="job-matching-service",
                company=job.company,
                title=job.title,
            )
        async with session_factory() as session:
            application = await ApplicationRepository(session).get_for_job(job.job_id)
            history = await ApplicationHistoryRepository(session).list_for_application(
                application.id
            )
            return application, history

    application, history = asyncio.run(_report_twice())

    assert application.status == ApplicationStatus.BLOCKED_LLM_NOT_CONFIGURED
    assert len(history) == 1
    assert history[0].from_status is None


def test_reporting_never_raises_when_no_reporter_is_registered() -> None:
    """A component-level process with no Tracking Service wired in still
    logs the block and returns normally — reporting must never become a new
    way for a consumer to fail."""
    from shared.contracts.opportunity_blocked import (
        OpportunityBlockedReason,
        report_opportunity_blocked,
        set_opportunity_blocked_reporter,
    )

    set_opportunity_blocked_reporter(None)
    asyncio.run(
        report_opportunity_blocked(
            job_id=uuid4(),
            user_id=UserId(uuid4()),
            reason=OpportunityBlockedReason.LLM_NOT_CONFIGURED,
            reported_by="job-matching-service",
        )
    )


def test_reporting_never_raises_when_the_reporter_fails() -> None:
    """Nor when the registered reporter itself blows up: the handler already
    decided this message must not fail, and bookkeeping cannot overrule it."""
    from shared.contracts.opportunity_blocked import (
        OpportunityBlockedReason,
        report_opportunity_blocked,
        set_opportunity_blocked_reporter,
    )

    calls: list[object] = []

    async def exploding_reporter(**kwargs: object) -> None:
        calls.append(kwargs)
        raise RuntimeError("tracking database unreachable")

    set_opportunity_blocked_reporter(exploding_reporter)
    try:
        asyncio.run(
            report_opportunity_blocked(
                job_id=uuid4(),
                user_id=UserId(uuid4()),
                reason=OpportunityBlockedReason.LLM_NOT_CONFIGURED,
                reported_by="outreach-service",
            )
        )
    finally:
        set_opportunity_blocked_reporter(None)

    assert len(calls) == 1
