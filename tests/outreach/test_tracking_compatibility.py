"""Cross-component compatibility test (Scenario L, this component's task
brief): `outreach.generated`/`outreach.approved`/`outreach.sent` payloads,
constructed exactly as `src/outreach/` publishes them, are fed through
`tracking.consumers`'s real handlers against a real (SQLite in-memory)
database and an in-memory Kafka broker, proving genuine end-to-end payload
compatibility — not just "the types match on paper".

This is a deliberate, test-only cross-component import
(`tracking.consumers`/`tracking.db`/`tracking.models`/`tracking.repository`)
— production code under `src/outreach/` never imports another component's
module (dependency-graph.md#1-compileimport-dependencies); this test tree
is not production code and is not owned by Outreach Service's runtime
boundary.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio

from infrastructure.kafka.serialization import build_envelope
from infrastructure.kafka.topics import Topic
from shared.events.payloads import OutreachDecision, OutreachSentConfirmation
from shared.types.dto import OutreachDraft
from shared.types.enums import ApplicationStatus, OutreachChannel, OutreachDecisionType
from shared.types.ids import ContactId, JobId, OutreachId, UserId

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def tracking_session_factory():
    pytest.importorskip(
        "aiosqlite", reason="async sqlite driver not installed; DB tests need it"
    )
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    try:
        from infrastructure.database import Base
    except ImportError as exc:
        pytest.skip(f"infrastructure.database.Base not available yet: {exc}")

    from tracking.models import ApplicationHistoryRecord, ApplicationRecord

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            tables=[ApplicationRecord.__table__, ApplicationHistoryRecord.__table__],
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_tracking_singletons() -> Iterator[None]:
    yield
    import tracking.db as tracking_db_module
    import tracking.events as tracking_events_module

    tracking_db_module.set_session_factory(None)
    tracking_events_module.set_event_producer(None)


async def test_outreach_event_payloads_are_tracking_compatible_end_to_end(
    tracking_session_factory,
) -> None:
    import tracking.consumers as tracking_consumers_module
    import tracking.db as tracking_db_module
    import tracking.events as tracking_events_module
    from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
    from infrastructure.kafka.producer import EventProducer
    from tracking.repository import ApplicationRepository

    tracking_db_module.set_session_factory(tracking_session_factory)
    broker = InMemoryBroker()
    tracking_events_module.set_event_producer(
        EventProducer("tracking-service", client=InMemoryProducerClient(broker))
    )

    job_id = JobId(uuid4())
    user_id = UserId(uuid4())
    outreach_id = OutreachId(uuid4())
    contact_id = ContactId(uuid4())

    # 1. outreach.generated — exact OutreachDraft shape
    #    workflows.langgraph.outreach_generation.nodes.persist_and_publish
    #    constructs and publishes.
    draft = OutreachDraft(
        outreach_id=outreach_id,
        job_id=job_id,
        contact_id=contact_id,
        user_id=user_id,
        channel=OutreachChannel.LINKEDIN_CONNECTION_REQUEST,
        draft_message="Hi Jordan, I'd love to connect about the role at Acme.",
        generated_at=datetime.now(UTC),
    )
    envelope = build_envelope(Topic.OUTREACH_GENERATED, draft, producer="outreach-service")
    await tracking_consumers_module._handle_outreach_generated_async(envelope)

    async with tracking_session_factory() as session:
        application = await ApplicationRepository(session).get_for_job(job_id)
    assert application is not None
    assert application.status == ApplicationStatus.OUTREACH_GENERATED

    # 2. outreach.approved — exact OutreachDecision shape
    #    outreach.api.routes.approve_outreach constructs and publishes.
    decision = OutreachDecision(
        outreach_id=outreach_id,
        job_id=job_id,
        user_id=user_id,
        decision=OutreachDecisionType.APPROVED,
        final_message=None,
        decided_at=datetime.now(UTC),
        decided_by=user_id,
    )
    envelope = build_envelope(Topic.OUTREACH_APPROVED, decision, producer="outreach-service")
    await tracking_consumers_module._handle_outreach_approved_async(envelope)

    async with tracking_session_factory() as session:
        application = await ApplicationRepository(session).get_for_job(job_id)
    assert application.status == ApplicationStatus.OUTREACH_APPROVED

    # 3. outreach.sent — exact OutreachSentConfirmation shape
    #    outreach.consumers._handle_outreach_approved_async's send-worker
    #    body constructs and publishes.
    confirmation = OutreachSentConfirmation(
        outreach_id=outreach_id,
        job_id=job_id,
        user_id=user_id,
        channel=OutreachChannel.LINKEDIN_CONNECTION_REQUEST,
        sent_at=datetime.now(UTC),
        external_message_id="ext-1",
    )
    envelope = build_envelope(Topic.OUTREACH_SENT, confirmation, producer="outreach-service")
    await tracking_consumers_module._handle_outreach_sent_async(envelope)

    async with tracking_session_factory() as session:
        application = await ApplicationRepository(session).get_for_job(job_id)
    assert application.status == ApplicationStatus.OUTREACH_SENT

    # All three transitions produced a real, correctly-shaped
    # ApplicationUpdatedEvent — proof the whole chain deserializes and
    # applies cleanly through Tracking's own code, not just "types match
    # on paper".
    assert len(broker.log(Topic.APPLICATIONS_UPDATED.value)) == 3
