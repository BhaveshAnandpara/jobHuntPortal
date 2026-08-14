"""Tests for `jobs.ingestion.events.publish_job_discovered` — the
`jobs.discovered` producer boundary for the manual URL path. Uses the real
`EventProducer` against the in-memory fake broker (no live Kafka).
"""

from datetime import UTC, datetime
from uuid import uuid4

from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize
from infrastructure.kafka.topics import Topic
from jobs.ingestion.events import PRODUCER_NAME, SOURCE_TAG, publish_job_discovered
from shared.types.dto import NormalizedJob
from shared.types.enums import EventType, JobSourceType
from shared.types.ids import CorrelationId


def _normalized_job() -> NormalizedJob:
    return NormalizedJob(
        job_id=uuid4(),
        user_id=uuid4(),
        company="Acme Robotics",
        title="Senior Mechanical Engineer",
        description="Design and validate mechanical subsystems.",
        source_type=JobSourceType.MANUAL_URL,
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


def test_publish_job_discovered_mints_a_new_correlation_id_by_default() -> None:
    broker = InMemoryBroker()
    producer = EventProducer(PRODUCER_NAME, client=InMemoryProducerClient(broker))

    first = publish_job_discovered(_normalized_job(), producer=producer)
    second = publish_job_discovered(_normalized_job(), producer=producer)

    assert first.correlation_id != second.correlation_id


def test_publish_job_discovered_propagates_explicit_correlation_id() -> None:
    broker = InMemoryBroker()
    producer = EventProducer(PRODUCER_NAME, client=InMemoryProducerClient(broker))
    correlation_id = CorrelationId(uuid4())

    envelope = publish_job_discovered(
        _normalized_job(), producer=producer, correlation_id=correlation_id
    )

    assert envelope.correlation_id == correlation_id


def test_publish_job_discovered_partitions_by_job_id() -> None:
    broker = InMemoryBroker()
    producer = EventProducer(PRODUCER_NAME, client=InMemoryProducerClient(broker))
    payload = _normalized_job()

    publish_job_discovered(payload, producer=producer)

    log = broker.log(Topic.JOBS_DISCOVERED.value)
    assert log[0].key() == str(payload.job_id).encode("utf-8")
