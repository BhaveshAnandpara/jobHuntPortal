"""Per-message exception isolation: nothing one message does may end a
consumer's polling loop.

`test_consumer_retry_dlq.py` already covers the *handler's* own exceptions
(retry, then dead-letter). This module covers the rest of `_process_message`
— the DLQ republish and the offset commit — which run outside that retry
`try` and, before `poll_once`'s guard, escaped all the way out of
`EventConsumer.run()`. `scripts/run_consumers.py` only catches at the thread
level (its `_run` wraps the whole `consumer.run()` call), so an escape there
logged "Consumer crashed" and permanently stopped that (topic, group_id)
pair for the lifetime of the process — one unlucky message taking every
other user's events down with it.
"""

from __future__ import annotations

from infrastructure.kafka.consumer import DEFAULT_MAX_ATTEMPTS, EventConsumer
from infrastructure.kafka.in_memory import (
    InMemoryBroker,
    InMemoryConsumerClient,
    InMemoryProducerClient,
)
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize
from infrastructure.kafka.topics import Topic, dlq_topic
from shared.events.envelope import EventEnvelope
from tests.infrastructure.kafka.conftest import make_normalized_job


class _FlakyDlqProducer:
    """`EventProducer`-shaped wrapper whose first `publish_raw` fails.

    Models the realistic case the guard exists for: the handler failed, the
    consumer went to dead-letter the message, and *that* publish could not
    reach the broker.
    """

    def __init__(self, inner: EventProducer, *, failures: int = 1) -> None:
        self._inner = inner
        self._remaining = failures
        self.attempts = 0

    def publish_raw(self, topic_name, value, *, key=None, headers=None):
        self.attempts += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise RuntimeError("DLQ broker unreachable")
        return self._inner.publish_raw(topic_name, value, key=key, headers=headers)


def _consumer(broker: InMemoryBroker, handler, *, dlq_producer) -> EventConsumer:
    return EventConsumer(
        Topic.JOBS_DISCOVERED,
        "job-matching-service",
        handler,
        client=InMemoryConsumerClient(broker, "job-matching-service"),
        dlq_producer=dlq_producer,
        sleep=lambda _seconds: None,
    )


def test_dlq_publish_failure_does_not_end_the_polling_loop() -> None:
    """The regression this guards: a failing DLQ republish used to escape
    `_process_message` -> `poll_once` -> `run()` and kill the thread.

    The first message's handler always fails, and the dead-letter publish
    itself fails the first time it is attempted. The loop must survive that,
    retry the still-uncommitted message, and then go on to deliver the
    *second*, unrelated message to the handler successfully.
    """
    broker = InMemoryBroker()
    inner = EventProducer("job-ingestion-service", client=InMemoryProducerClient(broker))
    poison = make_normalized_job()
    healthy = make_normalized_job()
    inner.publish(Topic.JOBS_DISCOVERED, poison)
    inner.publish(Topic.JOBS_DISCOVERED, healthy)

    succeeded: list[EventEnvelope] = []

    def handler(envelope: EventEnvelope) -> None:
        if envelope.payload.job_id == poison.job_id:
            raise RuntimeError("workflow blew up")
        succeeded.append(envelope)

    dlq_producer = _FlakyDlqProducer(inner)
    consumer = _consumer(broker, handler, dlq_producer=dlq_producer)

    # Would have raised out of run() before poll_once's guard existed.
    processed = consumer.run(max_messages=3)

    # 1: poison, DLQ publish fails -> abandoned, offset uncommitted.
    # 2: poison redelivered, DLQ publish succeeds -> committed.
    # 3: the healthy message, handled normally.
    assert processed == 3
    assert dlq_producer.attempts == 2
    assert [envelope.payload.job_id for envelope in succeeded] == [healthy.job_id]

    dlq_messages = broker.log(dlq_topic(Topic.JOBS_DISCOVERED))
    assert len(dlq_messages) == 1
    assert deserialize(Topic.JOBS_DISCOVERED, dlq_messages[0].value()).payload == poison

    # Both offsets committed once the loop finished: nothing left to poll.
    assert consumer.poll_once() is False


def test_commit_failure_does_not_end_the_polling_loop() -> None:
    """The other unguarded step: the offset commit itself raising."""
    broker = InMemoryBroker()
    producer = EventProducer("job-ingestion-service", client=InMemoryProducerClient(broker))
    first = make_normalized_job()
    second = make_normalized_job()
    producer.publish(Topic.JOBS_DISCOVERED, first)
    producer.publish(Topic.JOBS_DISCOVERED, second)

    class _FlakyCommitClient(InMemoryConsumerClient):
        """Commits, then raises the first time — modelling a client that
        reached the broker but failed on the way back. Advancing before
        raising is what keeps this test deterministic rather than looping
        forever on the same message."""

        def __init__(self, broker: InMemoryBroker, group_id: str) -> None:
            super().__init__(broker, group_id)
            self.failures = 0

        def commit(self, message=None, asynchronous: bool = True) -> None:
            super().commit(message, asynchronous)
            if self.failures == 0:
                self.failures += 1
                raise RuntimeError("offset commit failed")

    handled: list[EventEnvelope] = []
    client = _FlakyCommitClient(broker, "job-matching-service")
    consumer = EventConsumer(
        Topic.JOBS_DISCOVERED,
        "job-matching-service",
        handled.append,
        client=client,
        dlq_producer=producer,
        sleep=lambda _seconds: None,
    )

    processed = consumer.run(max_messages=2)

    assert processed == 2
    assert client.failures == 1
    assert [envelope.payload.job_id for envelope in handled] == [
        first.job_id,
        second.job_id,
    ]


def test_handler_failure_is_already_isolated_and_the_loop_continues() -> None:
    """Documents the behaviour that was *already* correct before this task:
    an unhandled handler exception is caught per message inside
    `_process_message`, retried `DEFAULT_MAX_ATTEMPTS` times, dead-lettered,
    and the loop moves straight on to the next message.
    """
    broker = InMemoryBroker()
    producer = EventProducer("job-ingestion-service", client=InMemoryProducerClient(broker))
    poison = make_normalized_job()
    healthy = make_normalized_job()
    producer.publish(Topic.JOBS_DISCOVERED, poison)
    producer.publish(Topic.JOBS_DISCOVERED, healthy)

    attempts: list[object] = []
    succeeded: list[EventEnvelope] = []

    def handler(envelope: EventEnvelope) -> None:
        attempts.append(envelope.payload.job_id)
        if envelope.payload.job_id == poison.job_id:
            raise RuntimeError("workflow blew up")
        succeeded.append(envelope)

    consumer = _consumer(broker, handler, dlq_producer=producer)
    processed = consumer.run(max_messages=2)

    assert processed == 2
    assert attempts.count(poison.job_id) == DEFAULT_MAX_ATTEMPTS
    assert [envelope.payload.job_id for envelope in succeeded] == [healthy.job_id]
    assert len(broker.log(dlq_topic(Topic.JOBS_DISCOVERED))) == 1
