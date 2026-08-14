"""Tests for the `profiles.updated` producer (profiles/events.py).

No live Kafka broker required — EventProducer is wired to
tests/profiles/conftest.py's FakeProducerClient.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.topics import Topic
from profiles.events import publish_profile_updated
from shared.events.payloads import ProfileUpdateSummary
from shared.types.enums import EventType, ProfileChangeType
from shared.types.ids import ProfileId, ResumeId, UserId
from tests.profiles.conftest import FakeProducerClient


def _payload(change_type: ProfileChangeType) -> ProfileUpdateSummary:
    return ProfileUpdateSummary(
        profile_id=ProfileId(uuid4()),
        user_id=UserId(uuid4()),
        resume_id=ResumeId(uuid4()),
        change_type=change_type,
        updated_at=datetime.now(UTC),
    )


def test_publish_profile_updated_created_envelope_shape(
    event_producer: EventProducer, fake_producer_client: FakeProducerClient
) -> None:
    payload = _payload(ProfileChangeType.CREATED)

    envelope = publish_profile_updated(payload, producer=event_producer)

    assert envelope.event_type is EventType.PROFILE_UPDATED
    assert envelope.payload == payload
    assert envelope.user_id == payload.user_id
    assert envelope.metadata.producer == "resume-profile-service"

    assert len(fake_producer_client.produced) == 1
    topic_name, value, key = fake_producer_client.produced[0]
    assert topic_name == Topic.PROFILES_UPDATED.value
    # partition key is user_id per kafka-topics.md#profilesupdated
    assert key == str(payload.user_id).encode("utf-8")
    assert value is not None
    assert b"PROFILE_UPDATED" in value


def test_publish_profile_updated_archived(
    event_producer: EventProducer, fake_producer_client: FakeProducerClient
) -> None:
    payload = _payload(ProfileChangeType.ARCHIVED)

    envelope = publish_profile_updated(payload, producer=event_producer)

    assert envelope.payload.change_type is ProfileChangeType.ARCHIVED
    assert len(fake_producer_client.produced) == 1


def test_publish_profile_updated_propagates_correlation_id(
    event_producer: EventProducer, fake_producer_client: FakeProducerClient
) -> None:
    from shared.types.ids import CorrelationId

    correlation_id = CorrelationId(uuid4())
    payload = _payload(ProfileChangeType.CREATED)

    envelope = publish_profile_updated(payload, producer=event_producer, correlation_id=correlation_id)

    assert envelope.correlation_id == correlation_id
