"""Kafka producer boundary for Tracking Service.

Produces (docs/architecture/event-contracts.md,
docs/architecture/kafka-topics.md):
    `applications.updated` (ApplicationUpdatedEvent, payload
    ApplicationStatusUpdate) — on every real status change, from either a
    consumed upstream event (tracking.consumers) or a manual
    `PATCH /applications/{id}/status` call (tracking.api.routes).
    Reserved for future Analytics/notifications; no required consumer
    exists today (shared-types.md#applicationstatusupdate).
"""

from __future__ import annotations

from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.topics import Topic
from shared.events.envelope import EventEnvelope
from shared.types.dto import ApplicationStatusUpdate
from shared.types.ids import CorrelationId

PRODUCER_NAME = "tracking-service"

_producer: EventProducer | None = None


def get_event_producer() -> EventProducer:
    """Process-wide `EventProducer`, lazily constructed on first use so
    importing this module never requires a reachable Kafka broker — same
    pattern as `matching/events.py:get_event_producer`.
    """
    global _producer
    if _producer is None:
        _producer = EventProducer(PRODUCER_NAME)
    return _producer


def set_event_producer(producer: EventProducer | None) -> None:
    """Test seam — inject a fake/in-memory-broker-backed `EventProducer`.
    Pass `None` to restore the lazily-constructed default.
    """
    global _producer
    _producer = producer


async def publish_application_updated(
    payload: ApplicationStatusUpdate,
    *,
    correlation_id: CorrelationId | None = None,
) -> EventEnvelope[ApplicationStatusUpdate]:
    """Publish `ApplicationStatusUpdate` to `applications.updated`.

    `correlation_id` should be the value obtained from
    `infrastructure.kafka.consumer.propagate_correlation_id` on the inbound
    envelope that caused this transition (event-contracts.md's
    correlation_id propagation rule) for every event-triggered call.
    `None` is passed only for the one legitimate "starts a new causal
    chain" case: a manual `PATCH /applications/{id}/status` call, which has
    no inbound envelope to propagate from — see `tracking.api.routes`,
    which mints a fresh id via
    `infrastructure.kafka.serialization.new_correlation_id` before calling
    this function (mirroring `matching/events.py:publish_job_matched`'s
    docstring note about this same case, except this is the one place in
    the system where it is actually exercised).
    """
    return get_event_producer().publish(
        Topic.APPLICATIONS_UPDATED, payload, correlation_id=correlation_id
    )


__all__ = ["PRODUCER_NAME", "get_event_producer", "publish_application_updated", "set_event_producer"]
