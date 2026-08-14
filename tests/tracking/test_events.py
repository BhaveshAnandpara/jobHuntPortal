"""Tests for `tracking.events.publish_application_updated` against the
Kafka infra's in-memory fake broker (`infrastructure.kafka.in_memory`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

import tracking.events as events_module
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize
from infrastructure.kafka.topics import Topic
from shared.types.dto import ApplicationStatusUpdate
from shared.types.enums import ApplicationStatus
from shared.types.ids import ApplicationId, CorrelationId, JobId, UserId

pytestmark = pytest.mark.asyncio


def _status_update(**overrides: object) -> ApplicationStatusUpdate:
    fields: dict[str, object] = {
        "application_id": ApplicationId(uuid4()),
        "job_id": JobId(uuid4()),
        "user_id": UserId(uuid4()),
        "previous_status": ApplicationStatus.DISCOVERED,
        "new_status": ApplicationStatus.MATCHED,
        "changed_at": datetime.now(UTC),
        "triggered_by": "job-matching-service",
    }
    fields.update(overrides)
    return ApplicationStatusUpdate(**fields)


async def test_publish_application_updated_publishes_to_applications_updated_topic() -> None:
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("tracking-service", client=InMemoryProducerClient(broker))
    )
    payload = _status_update()

    envelope = await events_module.publish_application_updated(payload)

    assert envelope.payload == payload
    messages = broker.log(Topic.APPLICATIONS_UPDATED.value)
    assert len(messages) == 1
    published = deserialize(Topic.APPLICATIONS_UPDATED, messages[0].value())
    assert published.payload.application_id == payload.application_id
    assert published.payload.new_status == ApplicationStatus.MATCHED


async def test_publish_application_updated_propagates_correlation_id() -> None:
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("tracking-service", client=InMemoryProducerClient(broker))
    )
    payload = _status_update()
    correlation_id = CorrelationId(uuid4())

    envelope = await events_module.publish_application_updated(
        payload, correlation_id=correlation_id
    )

    assert envelope.correlation_id == correlation_id
    published = deserialize(
        Topic.APPLICATIONS_UPDATED, broker.log(Topic.APPLICATIONS_UPDATED.value)[0].value()
    )
    assert published.correlation_id == correlation_id


async def test_publish_application_updated_mints_new_correlation_id_when_none_given() -> None:
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("tracking-service", client=InMemoryProducerClient(broker))
    )
    payload = _status_update()

    envelope = await events_module.publish_application_updated(payload)

    assert envelope.correlation_id is not None
