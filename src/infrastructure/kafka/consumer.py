"""Typed event consumer: subscribe, deserialize, retry, dead-letter.

Mirrors ``producer.py``'s shape. A handler receives a fully-typed
``EventEnvelope[T]`` — never a bare dict — and the wrapper owns everything
around it: consumer-group subscription, exponential-backoff retry on
handler failure, and dead-letter republishing once retries are exhausted,
per docs/architecture/kafka-topics.md's delivery-semantics section.
"""

import time
from collections.abc import Callable
from typing import Self

from infrastructure.kafka.client import ConsumerClient, Message
from infrastructure.kafka.config import KafkaConfig
from infrastructure.kafka.errors import EventDeserializationError
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize, serialize
from infrastructure.kafka.topics import Topic, dlq_topic
from infrastructure.logging import format_context, get_logger
from shared.errors.codes import ErrorCode
from shared.events.envelope import EventEnvelope
from shared.types.ids import CorrelationId

logger = get_logger(__name__)

EventHandler = Callable[[EventEnvelope], None]
"""A consumer's business logic: receives one deserialized event, does its
work (which may include calling ``EventProducer.publish`` for a downstream
event), and either returns normally (-> offset committed) or raises (->
retried, then dead-lettered after ``max_attempts``).
"""

DEFAULT_MAX_ATTEMPTS = 3
"""3 total attempts on a handler failure, per kafka-topics.md's retry
strategy — i.e. the original attempt plus 2 retries."""

DEFAULT_BACKOFF_BASE_SECONDS = 1.0
"""Exponential backoff base: attempt *n* waits
``DEFAULT_BACKOFF_BASE_SECONDS * 2 ** (n - 1)`` seconds before the next
attempt (1s, 2s, ... for the default base)."""


def propagate_correlation_id(envelope: EventEnvelope) -> CorrelationId:
    """The ``correlation_id`` to pass to ``EventProducer.publish`` for any
    event a handler produces downstream of ``envelope``.

    Per shared-types.md's correlation-id propagation rule: a causal chain
    (``jobs.discovered`` -> ``jobs.matched`` -> ... -> `applications.updated`)
    shares one ``correlation_id`` end to end. A handler that publishes a
    downstream event must pass this value through unchanged rather than
    letting ``EventProducer.publish`` mint a new one:

        def handle(envelope: EventEnvelope) -> None:
            ...
            producer.publish(
                next_topic,
                next_payload,
                correlation_id=propagate_correlation_id(envelope),
            )
    """
    return envelope.correlation_id


def _classify_error(exc: Exception) -> ErrorCode | None:
    """Best-effort mapping from a handler's exception to a shared
    ``ErrorCode``, for ``EventMetadata.error_code`` on DLQ republish.

    This is transport infrastructure — it must not know a business
    component's exception taxonomy. It only reads a duck-typed
    ``error_code`` attribute (a component's own domain error may set one,
    e.g. ``raise MatchingError(..., error_code=ErrorCode.MATCHING_FAILED)``)
    and falls back to ``None`` when the failure isn't classifiable, exactly
    as event-contracts.md documents ("when classifiable").
    """
    code = getattr(exc, "error_code", None)
    return code if isinstance(code, ErrorCode) else None


def _default_client(config: KafkaConfig, group_id: str) -> ConsumerClient:
    from confluent_kafka import Consumer

    return Consumer(config.consumer_config(group_id))


class EventConsumer:
    """Consumes one ``Topic`` under a consumer group and runs ``handler``
    for every message, with retry-then-DLQ per kafka-topics.md.

    ``group_id`` should follow the ``{component-name}`` convention from
    kafka-topics.md (e.g. ``"job-matching-service"``); pass a distinct
    ``group_id`` for a second independent consumer group on the same topic
    (e.g. Outreach Service's dedicated send-worker group on
    ``outreach.approved``).
    """

    def __init__(
        self,
        topic: Topic,
        group_id: str,
        handler: EventHandler,
        *,
        config: KafkaConfig | None = None,
        client: ConsumerClient | None = None,
        dlq_producer: EventProducer | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.topic = topic
        self.group_id = group_id
        self.handler = handler
        self.config = config or KafkaConfig.from_env()
        self._client = (
            client if client is not None else _default_client(self.config, group_id)
        )
        self._dlq_producer = dlq_producer
        self.max_attempts = max_attempts
        self.backoff_base_seconds = backoff_base_seconds
        self._sleep = sleep
        self._subscribed = False

    def _producer(self) -> EventProducer:
        if self._dlq_producer is None:
            # A component that never expects a DLQ path in tests can omit
            # this; production callers should always pass one explicitly
            # (typically the same EventProducer the component already uses
            # to publish its own downstream events).
            self._dlq_producer = EventProducer(self.group_id, config=self.config)
        return self._dlq_producer

    def subscribe(self) -> None:
        if not self._subscribed:
            self._client.subscribe([self.topic.value])
            self._subscribed = True

    def poll_once(self, timeout: float = 1.0) -> bool:
        """Poll for and fully process at most one message.

        Returns ``True`` if a message was handled (successfully or
        dead-lettered), ``False`` if none was available within ``timeout``.
        """
        self.subscribe()
        message = self._client.poll(timeout)
        if message is None:
            return False
        error = message.error()
        if error:
            # Broker/client-level condition (e.g. partition EOF), not a
            # message to hand to the handler. Nothing to commit.
            logger.debug("Kafka poll returned a non-fatal error: %s", error)
            return False
        self._process_message(message)
        return True

    def run(
        self,
        *,
        max_messages: int | None = None,
        poll_timeout: float = 1.0,
        stop: Callable[[], bool] | None = None,
    ) -> int:
        """Poll in a loop until ``max_messages`` have been processed or
        ``stop()`` returns ``True``.

        Long-running production use passes a ``stop`` callback (e.g. backed
        by a shutdown signal) and no ``max_messages``. Tests should prefer
        calling ``poll_once`` directly against a fake client with a known,
        finite message count.
        """
        processed = 0
        while max_messages is None or processed < max_messages:
            if stop is not None and stop():
                break
            if self.poll_once(poll_timeout):
                processed += 1
        return processed

    def _process_message(self, message: Message) -> None:
        raw_value = message.value() or b""
        raw_key = message.key()

        try:
            envelope = deserialize(self.topic, raw_value)
        except EventDeserializationError as exc:
            # Permanent failure (errors.py) — not retried, straight to DLQ.
            # The bytes can't be parsed, so there is no valid EventEnvelope
            # to attach EventMetadata.failure_reason to; publish_raw's own
            # contract is to preserve unparseable bytes verbatim, so the
            # reason travels as a header instead of in the (unreadable)
            # body.
            logger.warning(
                "Message on %s failed deserialization, routing to DLQ: %s",
                self.topic.value,
                exc,
            )
            self._producer().publish_raw(
                dlq_topic(self.topic),
                raw_value,
                key=raw_key,
                headers=[("failure_reason", str(exc).encode("utf-8")[:2000])],
            )
            self._commit(message)
            return

        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                self.handler(envelope)
            except Exception as exc:  # noqa: BLE001 - transport can't know the handler's exception taxonomy
                last_exc = exc
                logger.warning(
                    "Handler for %s failed (attempt %d/%d): %s",
                    self.topic.value,
                    attempt,
                    self.max_attempts,
                    exc,
                )
                if attempt < self.max_attempts:
                    self._sleep(self.backoff_base_seconds * (2 ** (attempt - 1)))
                continue
            else:
                logger.info(
                    "Kafka event consumed | %s",
                    format_context(
                        topic=self.topic.value,
                        event_type=envelope.event_type.value,
                        correlation_id=envelope.correlation_id,
                        group_id=self.group_id,
                    ),
                )
                self._commit(message)
                return

        assert last_exc is not None
        logger.error(
            "Kafka event dead-lettered after %d attempt(s) | %s",
            self.max_attempts,
            format_context(
                topic=self.topic.value,
                event_type=envelope.event_type.value,
                correlation_id=envelope.correlation_id,
                error=str(last_exc),
            ),
        )
        self._dead_letter(envelope, raw_key, last_exc, retry_count=self.max_attempts)
        self._commit(message)

    def _dead_letter(
        self,
        envelope: EventEnvelope,
        key: bytes | None,
        exc: Exception,
        *,
        retry_count: int,
    ) -> None:
        dlq_envelope = envelope.model_copy(
            update={
                "metadata": envelope.metadata.model_copy(
                    update={
                        "retry_count": retry_count,
                        "failure_reason": str(exc),
                        "error_code": _classify_error(exc),
                    }
                )
            }
        )
        self._producer().publish_raw(
            dlq_topic(self.topic),
            serialize(dlq_envelope),
            key=key,
        )

    def _commit(self, message: Message) -> None:
        self._client.commit(message, asynchronous=False)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


__all__ = [
    "DEFAULT_BACKOFF_BASE_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "EventConsumer",
    "EventHandler",
    "propagate_correlation_id",
]
