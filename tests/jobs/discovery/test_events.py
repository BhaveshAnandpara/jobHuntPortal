"""Tests for `jobs.discovery.events.publish_job_discovered` — the
`jobs.discovered` producer boundary for the automatic path. Uses the real
`EventProducer` against the in-memory fake broker (no live Kafka).
"""

from datetime import UTC, datetime
from uuid import uuid4

from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize
from infrastructure.kafka.topics import Topic
from jobs.discovery.events import PRODUCER_NAME, SOURCE_TAG, publish_job_discovered
from shared.types.dto import NormalizedJob
from shared.types.enums import EventType, JobSourceType


def _normalized_job() -> NormalizedJob:
    return NormalizedJob(
        job_id=uuid4(),
        user_id=uuid4(),
        company="Acme Robotics",
        title="Senior Mechanical Engineer",
        description="Design and validate mechanical subsystems.",
        source_type=JobSourceType.LINKEDIN,
        source_url="https://boards.example.com/jobs/42",
        discovered_at=datetime.now(UTC),
    )


def test_publish_job_discovered_envelope_shape() -> None:
    broker = InMemoryBroker()
    producer = EventProducer(PRODUCER_NAME, client=InMemoryProducerClient(broker))
    payload = _normalized_job()

    envelope = publish_job_discovered(payload, producer=producer)

    assert envelope.event_type is EventType.JOB_DISCOVERED
    assert envelope.payload == payload
    assert envelope.metadata.producer == PRODUCER_NAME
    assert envelope.metadata.source == SOURCE_TAG

    log = broker.log(Topic.JOBS_DISCOVERED.value)
    assert len(log) == 1
    on_wire = deserialize(Topic.JOBS_DISCOVERED, log[0].value())
    assert on_wire == envelope


def test_publish_job_discovered_tags_source_automatic_not_manual() -> None:
    broker = InMemoryBroker()
    producer = EventProducer(PRODUCER_NAME, client=InMemoryProducerClient(broker))

    envelope = publish_job_discovered(_normalized_job(), producer=producer)

    assert envelope.metadata.source == "automatic"


def test_publish_job_discovered_partitions_by_job_id() -> None:
    broker = InMemoryBroker()
    producer = EventProducer(PRODUCER_NAME, client=InMemoryProducerClient(broker))
    payload = _normalized_job()

    publish_job_discovered(payload, producer=producer)

    log = broker.log(Topic.JOBS_DISCOVERED.value)
    assert log[0].key() == str(payload.job_id).encode("utf-8")
