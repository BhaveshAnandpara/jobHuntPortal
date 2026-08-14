"""Kafka producer boundary for Resume/Profile Service.

Produces: `profiles.updated` (ProfileUpdatedEvent, payload
ProfileUpdateSummary) — see docs/architecture/event-contracts.md#profileupdatedevent
and docs/architecture/kafka-topics.md#profilesupdated.

Emitted when a CandidateProfile is created or archived. Consumed by Job
Matching Service. This module only wraps the Kafka Infrastructure's
`EventProducer` (infrastructure/kafka/producer.py) with the one topic/payload
pair this component is allowed to publish — it never builds an envelope or
partition key itself (that's `infrastructure/kafka/serialization.py`'s job).
"""

from __future__ import annotations

from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.topics import Topic
from shared.events.envelope import EventEnvelope
from shared.events.payloads import ProfileUpdateSummary
from shared.types.ids import CorrelationId

PRODUCER_NAME = "resume-profile-service"


def publish_profile_updated(
    payload: ProfileUpdateSummary,
    *,
    producer: EventProducer,
    correlation_id: CorrelationId | None = None,
) -> EventEnvelope[ProfileUpdateSummary]:
    """Publish one `ProfileUpdateSummary` on `profiles.updated`.

    `producer` is injected by the caller (profiles/service.py) rather than
    constructed here, so tests can pass an `EventProducer` wired to a fake
    `ProducerClient` without a live broker.
    """
    return producer.publish(
        Topic.PROFILES_UPDATED,
        payload,
        correlation_id=correlation_id,
        source=None,
    )


__all__ = ["PRODUCER_NAME", "publish_profile_updated"]
