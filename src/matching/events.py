"""Kafka producer boundary for Job Matching Service.

Produces (docs/architecture/event-contracts.md,
docs/architecture/kafka-topics.md):
    `jobs.matched`        (JobMatchedEvent, payload JobMatchResult) — always
    `jobs.shortlisted`    (JobShortlistedEvent, payload JobMatchResult) —
                          only if recommendation == SHORTLIST
    `contacts.requested`  (ContactsRequestedEvent, payload
                          ContactSearchRequest) — only if recommendation ==
                          SHORTLIST, published alongside jobs.shortlisted

`publish_contacts_requested` is called from
`workflows.langgraph.job_matching.nodes.publish_match_result` (Wave 2
Contact Discovery cleanup — see kafka-topics.md's "profiles.updated and
contacts.requested — contract vs. current implementation status" section
for why this was a deferred stub until Contact Discovery Service existed).
"""

from __future__ import annotations

from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.topics import Topic
from shared.events.envelope import EventEnvelope
from shared.events.payloads import ContactSearchRequest
from shared.types.dto import JobMatchResult
from shared.types.ids import CorrelationId

PRODUCER_NAME = "job-matching-service"

_producer: EventProducer | None = None


def get_event_producer() -> EventProducer:
    """Process-wide `EventProducer`, lazily constructed on first use so
    importing this module never requires a reachable Kafka broker — same
    pattern as `profiles/api/dependencies.py:get_event_producer`.
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


async def publish_job_matched(
    payload: JobMatchResult,
    *,
    correlation_id: CorrelationId | None = None,
) -> EventEnvelope[JobMatchResult]:
    """Always published by `persist_and_publish`, regardless of
    `recommendation`. `correlation_id` should be the value obtained from
    `infrastructure.kafka.consumer.propagate_correlation_id` on the inbound
    `jobs.discovered` envelope (event-contracts.md's correlation_id
    propagation rule) — `None` only when this event legitimately starts a
    new causal chain (there is no such case in the current wiring).
    """
    return get_event_producer().publish(
        Topic.JOBS_MATCHED, payload, correlation_id=correlation_id
    )


async def publish_job_shortlisted(
    payload: JobMatchResult,
    *,
    correlation_id: CorrelationId | None = None,
) -> EventEnvelope[JobMatchResult]:
    """Published by `persist_and_publish` only when
    `recommendation == MatchRecommendation.SHORTLIST`, alongside
    `publish_job_matched` for the same result."""
    return get_event_producer().publish(
        Topic.JOBS_SHORTLISTED, payload, correlation_id=correlation_id
    )


async def publish_contacts_requested(
    payload: ContactSearchRequest,
    *,
    correlation_id: CorrelationId | None = None,
) -> EventEnvelope[ContactSearchRequest]:
    """Published by `persist_and_publish` (via `publish_match_result`) only
    when `recommendation == MatchRecommendation.SHORTLIST`, alongside
    `publish_job_shortlisted` for the same result — see
    kafka-topics.md#contactsrequested's design-decision note on why this is
    a distinct command topic rather than folded into `jobs.shortlisted`."""
    return get_event_producer().publish(
        Topic.CONTACTS_REQUESTED, payload, correlation_id=correlation_id
    )


__all__ = [
    "PRODUCER_NAME",
    "get_event_producer",
    "publish_contacts_requested",
    "publish_job_matched",
    "publish_job_shortlisted",
    "set_event_producer",
]
