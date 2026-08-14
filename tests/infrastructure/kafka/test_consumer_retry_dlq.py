"""Retry-then-DLQ: a handler that always raises gets exactly
DEFAULT_MAX_ATTEMPTS tries with exponential backoff, then the original event
lands on ``<topic>.dlq`` with EventMetadata.failure_reason/error_code
populated, and the offset is committed so the partition isn't blocked
(docs/architecture/kafka-topics.md's retry strategy).
"""



from infrastructure.kafka.consumer import DEFAULT_MAX_ATTEMPTS, EventConsumer
from infrastructure.kafka.in_memory import (
    InMemoryBroker,
    InMemoryConsumerClient,
    InMemoryProducerClient,
)
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize
from infrastructure.kafka.topics import Topic, dlq_topic
from shared.errors.codes import ErrorCode
from tests.infrastructure.kafka.conftest import make_normalized_job


class _ClassifiableError(Exception):
    def __init__(self, message: str, error_code: ErrorCode) -> None:
        super().__init__(message)
        self.error_code = error_code


def test_handler_always_raising_is_retried_then_dead_lettered() -> None:
    broker = InMemoryBroker()
    producer = EventProducer("job-ingestion-service", client=InMemoryProducerClient(broker))
    payload = make_normalized_job()
    sent_envelope = producer.publish(Topic.JOBS_DISCOVERED, payload)

    attempts: list[int] = []
    sleeps: list[float] = []

    def always_fails(envelope) -> None:
        attempts.append(1)
        raise RuntimeError("transient LLM timeout")

    consumer = EventConsumer(
        Topic.JOBS_DISCOVERED,
        "job-matching-service",
        always_fails,
        client=InMemoryConsumerClient(broker, "job-matching-service"),
        dlq_producer=producer,
        sleep=sleeps.append,
    )

    processed = consumer.poll_once()

    assert processed is True
    assert len(attempts) == DEFAULT_MAX_ATTEMPTS == 3
    # Backoff called between attempts only: 2 sleeps for 3 attempts.
    assert len(sleeps) == DEFAULT_MAX_ATTEMPTS - 1
    assert sleeps == sorted(sleeps)  # non-decreasing -> exponential backoff

    dlq_messages = broker.log(dlq_topic(Topic.JOBS_DISCOVERED))
    assert len(dlq_messages) == 1
    dlq_envelope = deserialize(Topic.JOBS_DISCOVERED, dlq_messages[0].value())

    assert dlq_envelope.event_id == sent_envelope.event_id
    assert dlq_envelope.payload == payload
    assert dlq_envelope.metadata.retry_count == DEFAULT_MAX_ATTEMPTS
    assert dlq_envelope.metadata.failure_reason == "transient LLM timeout"
    assert dlq_envelope.metadata.error_code is None  # not classifiable -> None

    # Offset committed despite the failure -> the partition isn't blocked.
    assert consumer.poll_once() is False


def test_dlq_populates_error_code_when_classifiable() -> None:
    broker = InMemoryBroker()
    producer = EventProducer("job-ingestion-service", client=InMemoryProducerClient(broker))
    payload = make_normalized_job()
    producer.publish(Topic.JOBS_DISCOVERED, payload)

    def always_fails_with_code(envelope) -> None:
        raise _ClassifiableError("matching workflow blew up", ErrorCode.MATCHING_FAILED)

    consumer = EventConsumer(
        Topic.JOBS_DISCOVERED,
        "job-matching-service",
        always_fails_with_code,
        client=InMemoryConsumerClient(broker, "job-matching-service"),
        dlq_producer=producer,
        sleep=lambda _seconds: None,
    )
    consumer.poll_once()

    dlq_messages = broker.log(dlq_topic(Topic.JOBS_DISCOVERED))
    dlq_envelope = deserialize(Topic.JOBS_DISCOVERED, dlq_messages[0].value())
    assert dlq_envelope.metadata.error_code == ErrorCode.MATCHING_FAILED
    assert dlq_envelope.metadata.failure_reason == "matching workflow blew up"


def test_handler_succeeding_on_a_later_attempt_is_not_dead_lettered() -> None:
    broker = InMemoryBroker()
    producer = EventProducer("job-ingestion-service", client=InMemoryProducerClient(broker))
    payload = make_normalized_job()
    producer.publish(Topic.JOBS_DISCOVERED, payload)

    calls = {"count": 0}

    def fails_once_then_succeeds(envelope) -> None:
        calls["count"] += 1
        if calls["count"] < 2:
            raise RuntimeError("transient DB error")

    consumer = EventConsumer(
        Topic.JOBS_DISCOVERED,
        "job-matching-service",
        fails_once_then_succeeds,
        client=InMemoryConsumerClient(broker, "job-matching-service"),
        dlq_producer=producer,
        sleep=lambda _seconds: None,
    )
    consumer.poll_once()

    assert calls["count"] == 2
    assert broker.log(dlq_topic(Topic.JOBS_DISCOVERED)) == []


def test_undeserializable_message_is_dead_lettered_without_calling_handler() -> None:
    """A permanently-malformed message (errors.py: EventDeserializationError)
    skips retry entirely and is routed straight to DLQ verbatim."""
    broker = InMemoryBroker()
    producer_client = InMemoryProducerClient(broker)
    producer_client.produce(Topic.JOBS_DISCOVERED.value, value=b"not valid json", key=b"whatever")

    dlq_producer = EventProducer("job-matching-service", client=producer_client)
    handler_calls: list = []

    consumer = EventConsumer(
        Topic.JOBS_DISCOVERED,
        "job-matching-service",
        handler_calls.append,
        client=InMemoryConsumerClient(broker, "job-matching-service"),
        dlq_producer=dlq_producer,
    )
    processed = consumer.poll_once()

    assert processed is True
    assert handler_calls == []  # handler never invoked for unparseable bytes
    dlq_messages = broker.log(dlq_topic(Topic.JOBS_DISCOVERED))
    assert len(dlq_messages) == 1
    assert dlq_messages[0].value() == b"not valid json"  # preserved verbatim
    assert dlq_messages[0].headers()[0][0] == "failure_reason"
