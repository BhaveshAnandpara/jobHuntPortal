"""Tests for `contacts.events` — `publish_contacts_found`/
`publish_contacts_requested` against the Kafka infra's in-memory fake
broker (`infrastructure.kafka.in_memory`). Mirrors
`tests/matching/test_events.py`'s pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

import contacts.events as events_module
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize
from infrastructure.kafka.topics import Topic
from shared.events.payloads import ContactSearchRequest
from shared.types.dto import ContactRankingResult, RankedContact
from shared.types.enums import ContactType
from shared.types.ids import ContactId, CorrelationId, JobId, UserId

pytestmark = pytest.mark.asyncio


def _ranking_result(**overrides: object) -> ContactRankingResult:
    fields: dict[str, object] = {
        "job_id": JobId(uuid4()),
        "user_id": UserId(uuid4()),
        "contacts": [
            RankedContact(
                contact_id=ContactId(uuid4()),
                full_name="Jordan Smith",
                headline="Mechanical Design Engineer",
                contact_type=ContactType.PRACTITIONER,
                profile_url=None,
                relevance_score=8.1,
            )
        ],
        "ranked_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return ContactRankingResult(**fields)


async def test_publish_contacts_found_publishes_to_contacts_found_topic() -> None:
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("contact-discovery-service", client=InMemoryProducerClient(broker))
    )
    result = _ranking_result()

    envelope = await events_module.publish_contacts_found(result)

    assert envelope.payload == result
    messages = broker.log(Topic.CONTACTS_FOUND.value)
    assert len(messages) == 1
    published = deserialize(Topic.CONTACTS_FOUND, messages[0].value())
    assert published.payload.job_id == result.job_id
    assert len(published.payload.contacts) == 1


async def test_publish_contacts_found_with_empty_contacts_list() -> None:
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("contact-discovery-service", client=InMemoryProducerClient(broker))
    )
    result = _ranking_result(contacts=[])

    await events_module.publish_contacts_found(result)

    published = deserialize(
        Topic.CONTACTS_FOUND, broker.log(Topic.CONTACTS_FOUND.value)[0].value()
    )
    assert published.payload.contacts == []


async def test_publish_contacts_found_propagates_correlation_id() -> None:
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("contact-discovery-service", client=InMemoryProducerClient(broker))
    )
    result = _ranking_result()
    correlation_id = CorrelationId(uuid4())

    envelope = await events_module.publish_contacts_found(result, correlation_id=correlation_id)

    assert envelope.correlation_id == correlation_id
    published = deserialize(
        Topic.CONTACTS_FOUND, broker.log(Topic.CONTACTS_FOUND.value)[0].value()
    )
    assert published.correlation_id == correlation_id


async def test_publish_contacts_requested_publishes_to_contacts_requested_topic() -> None:
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("contact-discovery-service", client=InMemoryProducerClient(broker))
    )
    payload = ContactSearchRequest(
        job_id=JobId(uuid4()), user_id=UserId(uuid4()), company="Acme", title="Engineer"
    )

    envelope = await events_module.publish_contacts_requested(payload)

    assert envelope.payload == payload
    messages = broker.log(Topic.CONTACTS_REQUESTED.value)
    assert len(messages) == 1
    published = deserialize(Topic.CONTACTS_REQUESTED, messages[0].value())
    assert published.payload.job_id == payload.job_id
