"""Correlation-id propagation across a causal chain
(jobs.discovered -> jobs.matched -> ...), per shared-types.md#identifiers.
"""

from infrastructure.kafka.consumer import EventConsumer, propagate_correlation_id
from infrastructure.kafka.in_memory import (
    InMemoryBroker,
    InMemoryConsumerClient,
    InMemoryProducerClient,
)
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.topics import Topic
from tests.infrastructure.kafka.conftest import (
    make_job_match_result,
    make_normalized_job,
)


def test_correlation_id_propagates_through_a_downstream_publish() -> None:
    broker = InMemoryBroker()
    ingestion_producer = EventProducer(
        "job-ingestion-service", client=InMemoryProducerClient(broker)
    )
    matching_producer = EventProducer(
        "job-matching-service", client=InMemoryProducerClient(broker)
    )

    origin_envelope = ingestion_producer.publish(Topic.JOBS_DISCOVERED, make_normalized_job())
    origin_correlation_id = origin_envelope.correlation_id

    # Job Matching Service's handler: consume jobs.discovered, publish
    # jobs.matched, propagating the correlation id it received.
    def handle_discovered(envelope) -> None:
        matching_producer.publish(
            Topic.JOBS_MATCHED,
            make_job_match_result(),
            correlation_id=propagate_correlation_id(envelope),
        )

    matching_consumer = EventConsumer(
        Topic.JOBS_DISCOVERED,
        "job-matching-service",
        handle_discovered,
        client=InMemoryConsumerClient(broker, "job-matching-service"),
    )
    assert matching_consumer.poll_once() is True

    downstream_received: list = []
    tracking_consumer = EventConsumer(
        Topic.JOBS_MATCHED,
        "tracking-service",
        downstream_received.append,
        client=InMemoryConsumerClient(broker, "tracking-service"),
    )
    assert tracking_consumer.poll_once() is True

    assert downstream_received[0].correlation_id == origin_correlation_id


def test_publish_without_correlation_id_mints_a_new_chain() -> None:
    broker = InMemoryBroker()
    producer = EventProducer("job-ingestion-service", client=InMemoryProducerClient(broker))
    first = producer.publish(Topic.JOBS_DISCOVERED, make_normalized_job())
    second = producer.publish(Topic.JOBS_DISCOVERED, make_normalized_job())
    assert first.correlation_id != second.correlation_id
