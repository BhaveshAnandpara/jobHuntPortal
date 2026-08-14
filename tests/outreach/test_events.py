"""Tests for `outreach.events` — `publish_outreach_generated`/
`publish_outreach_approved`/`publish_outreach_sent` against the Kafka
infra's in-memory fake broker (`infrastructure.kafka.in_memory`). Mirrors
`tests/matching/test_events.py`'s pattern.

Covers part of Scenario J (Kafka): each event emitted with correct
payload/correlation_id.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

import outreach.events as events_module
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize
from infrastructure.kafka.topics import Topic
from shared.events.payloads import OutreachDecision, OutreachSentConfirmation
from shared.types.dto import OutreachDraft
from shared.types.enums import OutreachChannel, OutreachDecisionType
from shared.types.ids import ContactId, CorrelationId, JobId, OutreachId, UserId

pytestmark = pytest.mark.asyncio


def _draft(**overrides: object) -> OutreachDraft:
    fields: dict[str, object] = {
        "outreach_id": OutreachId(uuid4()),
        "job_id": JobId(uuid4()),
        "contact_id": ContactId(uuid4()),
        "user_id": UserId(uuid4()),
        "channel": OutreachChannel.LINKEDIN_CONNECTION_REQUEST,
        "draft_message": "Hi Jordan, ...",
        "generated_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return OutreachDraft(**fields)


def _decision(**overrides: object) -> OutreachDecision:
    fields: dict[str, object] = {
        "outreach_id": OutreachId(uuid4()),
        "job_id": JobId(uuid4()),
        "user_id": UserId(uuid4()),
        "decision": OutreachDecisionType.APPROVED,
        "final_message": None,
        "decided_at": datetime.now(UTC),
        "decided_by": UserId(uuid4()),
    }
    fields.update(overrides)
    return OutreachDecision(**fields)


def _confirmation(**overrides: object) -> OutreachSentConfirmation:
    fields: dict[str, object] = {
        "outreach_id": OutreachId(uuid4()),
        "job_id": JobId(uuid4()),
        "user_id": UserId(uuid4()),
        "channel": OutreachChannel.LINKEDIN_CONNECTION_REQUEST,
        "sent_at": datetime.now(UTC),
        "external_message_id": "ext-1",
    }
    fields.update(overrides)
    return OutreachSentConfirmation(**fields)


def _wire(broker: InMemoryBroker) -> None:
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(broker))
    )


async def test_publish_outreach_generated_publishes_to_outreach_generated_topic() -> None:
    broker = InMemoryBroker()
    _wire(broker)
    draft = _draft()

    envelope = await events_module.publish_outreach_generated(draft)

    assert envelope.payload == draft
    messages = broker.log(Topic.OUTREACH_GENERATED.value)
    assert len(messages) == 1
    published = deserialize(Topic.OUTREACH_GENERATED, messages[0].value())
    assert published.payload.job_id == draft.job_id
    assert published.payload.channel == draft.channel


async def test_publish_outreach_generated_propagates_correlation_id() -> None:
    broker = InMemoryBroker()
    _wire(broker)
    draft = _draft()
    correlation_id = CorrelationId(uuid4())

    envelope = await events_module.publish_outreach_generated(draft, correlation_id=correlation_id)

    assert envelope.correlation_id == correlation_id
    published = deserialize(
        Topic.OUTREACH_GENERATED, broker.log(Topic.OUTREACH_GENERATED.value)[0].value()
    )
    assert published.correlation_id == correlation_id


async def test_publish_outreach_approved_publishes_to_outreach_approved_topic() -> None:
    broker = InMemoryBroker()
    _wire(broker)
    decision = _decision()

    envelope = await events_module.publish_outreach_approved(decision)

    assert envelope.payload == decision
    messages = broker.log(Topic.OUTREACH_APPROVED.value)
    assert len(messages) == 1
    published = deserialize(Topic.OUTREACH_APPROVED, messages[0].value())
    assert published.payload.decision == OutreachDecisionType.APPROVED


async def test_publish_outreach_approved_without_correlation_id_mints_a_fresh_one() -> None:
    """The /approve handler has no inbound Kafka envelope to propagate
    from — a new causal chain origin, same as Tracking Service's manual
    PATCH endpoint (see events.py's docstring)."""
    broker = InMemoryBroker()
    _wire(broker)
    decision = _decision()

    envelope = await events_module.publish_outreach_approved(decision)

    assert envelope.correlation_id is not None


async def test_publish_outreach_sent_publishes_to_outreach_sent_topic() -> None:
    broker = InMemoryBroker()
    _wire(broker)
    confirmation = _confirmation()

    envelope = await events_module.publish_outreach_sent(confirmation)

    assert envelope.payload == confirmation
    messages = broker.log(Topic.OUTREACH_SENT.value)
    assert len(messages) == 1
    published = deserialize(Topic.OUTREACH_SENT, messages[0].value())
    assert published.payload.external_message_id == "ext-1"


async def test_publish_outreach_sent_propagates_correlation_id() -> None:
    broker = InMemoryBroker()
    _wire(broker)
    confirmation = _confirmation()
    correlation_id = CorrelationId(uuid4())

    envelope = await events_module.publish_outreach_sent(confirmation, correlation_id=correlation_id)

    assert envelope.correlation_id == correlation_id
