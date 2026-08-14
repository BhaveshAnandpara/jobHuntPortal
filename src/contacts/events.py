"""Kafka producer boundary for Contact Discovery Service.

Produces:
    `contacts.found`     (ContactsFoundEvent, payload ContactRankingResult)
                          — from workflows.langgraph.contact_discovery.nodes
                          .persist_and_publish; see
                          docs/architecture/event-contracts.md#contactsfoundevent.
                          `contacts` may be an empty list — that is the
                          documented "no contacts found" signal, not an
                          absent field.
    `contacts.requested` (ContactsRequestedEvent, payload
                          ContactSearchRequest) — from this component's own
                          `POST /jobs/{job_id}/contacts/search` manual
                          re-trigger (contacts/api/routes.py), a
                          passthrough publish, not a self-call.
                          docs/architecture/kafka-topics.md's
                          `contacts.requested` topic entry explicitly
                          grants this: "Producers: Job Matching Service
                          (automatic, on shortlist), Contact Discovery
                          Service (its own POST /jobs/{job_id}/contacts/
                          search publishes here, not a self-call)".
"""

from __future__ import annotations

from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.topics import Topic
from shared.events.envelope import EventEnvelope
from shared.events.payloads import ContactSearchRequest
from shared.types.dto import ContactRankingResult
from shared.types.ids import CorrelationId

PRODUCER_NAME = "contact-discovery-service"

_producer: EventProducer | None = None


def get_event_producer() -> EventProducer:
    """Process-wide `EventProducer`, lazily constructed on first use so
    importing this module never requires a reachable Kafka broker — same
    pattern as `matching.events.get_event_producer`.
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


async def publish_contacts_found(
    payload: ContactRankingResult,
    *,
    correlation_id: CorrelationId | None = None,
) -> EventEnvelope[ContactRankingResult]:
    """Always published by `persist_and_publish`, regardless of whether
    `contacts` is empty. `correlation_id` should be the value obtained from
    `infrastructure.kafka.consumer.propagate_correlation_id` on the inbound
    `contacts.requested` envelope (event-contracts.md's correlation_id
    propagation rule) — never a freshly minted one.
    """
    return get_event_producer().publish(
        Topic.CONTACTS_FOUND, payload, correlation_id=correlation_id
    )


async def publish_contacts_requested(
    payload: ContactSearchRequest,
    *,
    correlation_id: CorrelationId | None = None,
) -> EventEnvelope[ContactSearchRequest]:
    """Published only by the `POST /jobs/{job_id}/contacts/search` route
    handler — a manual re-trigger passthrough onto the same command topic
    Job Matching Service publishes to automatically on shortlist. Never
    called from this workflow's own LangGraph nodes (`search_contacts`
    *consumes* `contacts.requested`; it never produces it).
    """
    return get_event_producer().publish(
        Topic.CONTACTS_REQUESTED, payload, correlation_id=correlation_id
    )


__all__ = [
    "PRODUCER_NAME",
    "get_event_producer",
    "publish_contacts_found",
    "publish_contacts_requested",
    "set_event_producer",
]
