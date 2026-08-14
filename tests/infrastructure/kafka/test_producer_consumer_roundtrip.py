"""End-to-end transport validation: EventProducer.publish -> serialized
bytes on the (fake) broker -> EventConsumer deserializes and hands the
handler the identical canonical event. Covers every one of the ten topics.
"""

from datetime import UTC

import pytest

from infrastructure.kafka.consumer import EventConsumer
from infrastructure.kafka.in_memory import (
    InMemoryBroker,
    InMemoryConsumerClient,
    InMemoryProducerClient,
)
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.topics import all_specs
from tests.infrastructure.kafka.conftest import sample_payload


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.topic.value)
def test_round_trip_every_topic(spec) -> None:
    broker = InMemoryBroker()
    producer = EventProducer("origin-service", client=InMemoryProducerClient(broker))
    payload = sample_payload(spec.topic)

    sent_envelope = producer.publish(spec.topic, payload)

    received: list = []
    consumer = EventConsumer(
        spec.topic,
        "consuming-service",
        received.append,
        client=InMemoryConsumerClient(broker, "consuming-service"),
    )

    processed = consumer.poll_once()

    assert processed is True
    assert len(received) == 1
    assert received[0] == sent_envelope
    assert received[0].payload == payload
    # Offset committed -> a second poll finds nothing left for this group.
    assert consumer.poll_once() is False


def test_multiple_consumer_groups_are_independent() -> None:
    """Two independent consumer groups on the same topic (e.g. Tracking and
    a future Analytics service on jobs.matched) each see every message —
    kafka-topics.md's documented multi-consumer-group behavior."""
    from infrastructure.kafka.topics import Topic

    broker = InMemoryBroker()
    producer = EventProducer("job-matching-service", client=InMemoryProducerClient(broker))
    payload = sample_payload(Topic.JOBS_MATCHED)
    producer.publish(Topic.JOBS_MATCHED, payload)

    group_a_received: list = []
    group_b_received: list = []
    consumer_a = EventConsumer(
        Topic.JOBS_MATCHED,
        "tracking-service",
        group_a_received.append,
        client=InMemoryConsumerClient(broker, "tracking-service"),
    )
    consumer_b = EventConsumer(
        Topic.JOBS_MATCHED,
        "analytics-service",
        group_b_received.append,
        client=InMemoryConsumerClient(broker, "analytics-service"),
    )

    assert consumer_a.poll_once() is True
    assert consumer_b.poll_once() is True
    assert len(group_a_received) == 1
    assert len(group_b_received) == 1
    assert group_a_received[0] == group_b_received[0]


def test_ordering_preserved_within_a_partition() -> None:
    """Multiple events for the same job arrive in publish order — the
    per-partition ordering guarantee kafka-topics.md documents."""
    from datetime import datetime
    from uuid import uuid4

    from infrastructure.kafka.topics import Topic
    from shared.types.dto import NormalizedJob
    from shared.types.enums import JobSourceType

    broker = InMemoryBroker()
    producer = EventProducer("job-ingestion-service", client=InMemoryProducerClient(broker))
    job_id = uuid4()
    user_id = uuid4()

    published_titles = ["First posting seen", "Amended posting"]
    for title in published_titles:
        job = NormalizedJob(
            job_id=job_id,
            user_id=user_id,
            company="Acme",
            title=title,
            description="...",
            source_type=JobSourceType.MANUAL_URL,
            discovered_at=datetime.now(UTC),
        )
        producer.publish(Topic.JOBS_DISCOVERED, job)

    received: list = []
    consumer = EventConsumer(
        Topic.JOBS_DISCOVERED,
        "job-matching-service",
        received.append,
        client=InMemoryConsumerClient(broker, "job-matching-service"),
    )
    consumer.poll_once()
    consumer.poll_once()

    assert [e.payload.title for e in received] == published_titles
