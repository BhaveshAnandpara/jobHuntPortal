"""Kafka producer boundary for Outreach Service.

Produces (docs/architecture/event-contracts.md,
docs/architecture/kafka-topics.md):
    `outreach.generated` (OutreachGeneratedEvent, payload OutreachDraft) —
                          from workflows.langgraph.outreach_generation.nodes
                          .persist_and_publish
    `outreach.approved`  (OutreachApprovedEvent, payload OutreachDecision) —
                          published from the /approve API handler
                          (outreach/api/routes.py) — this is the "no inbound
                          envelope to propagate from, mint a fresh
                          correlation_id" origin of a new causal chain, same
                          pattern Tracking Service's manual PATCH endpoint
                          uses (event-contracts.md's correlation_id
                          propagation rule only applies when there IS an
                          inbound envelope)
    `outreach.sent`      (OutreachSentEvent, payload
                          OutreachSentConfirmation) — published only by the
                          send-worker consumer (outreach.consumers
                          .handle_outreach_approved's async body)

Note: rejection does not publish an event today — see
docs/architecture/component-contracts.md#post-outreachoutreach_idreject.
"""

from __future__ import annotations

from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.topics import Topic
from shared.events.envelope import EventEnvelope
from shared.events.payloads import OutreachDecision, OutreachSentConfirmation
from shared.types.dto import OutreachDraft
from shared.types.ids import CorrelationId

PRODUCER_NAME = "outreach-service"

_producer: EventProducer | None = None


def get_event_producer() -> EventProducer:
    """Process-wide `EventProducer`, lazily constructed on first use so
    importing this module never requires a reachable Kafka broker — same
    pattern as `matching.events.get_event_producer` / `contacts.events
    .get_event_producer`.
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


async def publish_outreach_generated(
    payload: OutreachDraft,
    *,
    correlation_id: CorrelationId | None = None,
) -> EventEnvelope[OutreachDraft]:
    """Always published by `persist_and_publish`, once per generated draft.
    `correlation_id` should be the value obtained from
    `infrastructure.kafka.consumer.propagate_correlation_id` on the inbound
    `contacts.found` envelope (event-contracts.md's correlation_id
    propagation rule) — never a freshly minted one.
    """
    return get_event_producer().publish(
        Topic.OUTREACH_GENERATED, payload, correlation_id=correlation_id
    )


async def publish_outreach_approved(
    payload: OutreachDecision,
    *,
    correlation_id: CorrelationId | None = None,
) -> EventEnvelope[OutreachDecision]:
    """Published only by the `POST /outreach/{outreach_id}/approve` route
    handler. `correlation_id` is `None` here in the ordinary case — the
    human approval action is a new causal-chain origin (there is no inbound
    Kafka envelope to propagate from at an HTTP request), so
    `EventProducer.publish`/`build_envelope` mints a fresh one, matching
    Tracking Service's manual `PATCH /applications/{id}/status` endpoint's
    documented precedent for the same situation.
    """
    return get_event_producer().publish(
        Topic.OUTREACH_APPROVED, payload, correlation_id=correlation_id
    )


async def publish_outreach_sent(
    payload: OutreachSentConfirmation,
    *,
    correlation_id: CorrelationId | None = None,
) -> EventEnvelope[OutreachSentConfirmation]:
    """Published only by the send-worker consumer
    (`outreach.consumers.handle_outreach_approved`'s async body), after a
    successful `MessageSendClient.send()`. `correlation_id` should be the
    value obtained from `propagate_correlation_id` on the inbound
    `outreach.approved` envelope.
    """
    return get_event_producer().publish(
        Topic.OUTREACH_SENT, payload, correlation_id=correlation_id
    )


__all__ = [
    "PRODUCER_NAME",
    "get_event_producer",
    "publish_outreach_approved",
    "publish_outreach_generated",
    "publish_outreach_sent",
    "set_event_producer",
]
