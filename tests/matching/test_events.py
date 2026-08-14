"""Tests for `matching.events` — `publish_job_matched`/`publish_job_shortlisted`/
`publish_contacts_requested` against the Kafka infra's in-memory fake broker
(`infrastructure.kafka.in_memory`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

import matching.events as events_module
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize
from infrastructure.kafka.topics import Topic
from shared.events.payloads import ContactSearchRequest
from shared.types.dto import JobMatchResult
from shared.types.enums import MatchRecommendation
from shared.types.ids import (
    CorrelationId,
    JobId,
    JobMatchId,
    ProfileId,
    ResumeId,
    UserId,
)

pytestmark = pytest.mark.asyncio


def _result(**overrides: object) -> JobMatchResult:
    fields: dict[str, object] = {
        "job_match_id": JobMatchId(uuid4()),
        "job_id": JobId(uuid4()),
        "user_id": UserId(uuid4()),
        "selected_profile_id": ProfileId(uuid4()),
        "selected_resume_id": ResumeId(uuid4()),
        "match_score": 0.9,
        "matched_skills": ["CAD"],
        "missing_skills": [],
        "recommendation": MatchRecommendation.SHORTLIST,
        "matched_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return JobMatchResult(**fields)


async def test_publish_job_matched_publishes_to_jobs_matched_topic() -> None:
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("job-matching-service", client=InMemoryProducerClient(broker))
    )
    result = _result()

    envelope = await events_module.publish_job_matched(result)

    assert envelope.payload == result
    messages = broker.log(Topic.JOBS_MATCHED.value)
    assert len(messages) == 1
    published = deserialize(Topic.JOBS_MATCHED, messages[0].value())
    assert published.payload.job_id == result.job_id


async def test_publish_job_shortlisted_publishes_to_jobs_shortlisted_topic() -> None:
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("job-matching-service", client=InMemoryProducerClient(broker))
    )
    result = _result(recommendation=MatchRecommendation.SHORTLIST)

    await events_module.publish_job_shortlisted(result)

    messages = broker.log(Topic.JOBS_SHORTLISTED.value)
    assert len(messages) == 1


async def test_publish_job_matched_propagates_correlation_id() -> None:
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("job-matching-service", client=InMemoryProducerClient(broker))
    )
    result = _result()
    correlation_id = CorrelationId(uuid4())

    envelope = await events_module.publish_job_matched(result, correlation_id=correlation_id)

    assert envelope.correlation_id == correlation_id
    published = deserialize(Topic.JOBS_MATCHED, broker.log(Topic.JOBS_MATCHED.value)[0].value())
    assert published.correlation_id == correlation_id


async def test_publish_contacts_requested_publishes_to_contacts_requested_topic() -> None:
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("job-matching-service", client=InMemoryProducerClient(broker))
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


async def test_publish_contacts_requested_propagates_correlation_id() -> None:
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("job-matching-service", client=InMemoryProducerClient(broker))
    )
    payload = ContactSearchRequest(
        job_id=JobId(uuid4()), user_id=UserId(uuid4()), company="Acme", title="Engineer"
    )
    correlation_id = CorrelationId(uuid4())

    envelope = await events_module.publish_contacts_requested(
        payload, correlation_id=correlation_id
    )

    assert envelope.correlation_id == correlation_id
