"""Typed event consumer: subscribe, deserialize, retry, dead-letter.

Mirrors ``producer.py``'s shape. A handler receives a fully-typed
``EventEnvelope[T]`` — never a bare dict — and the wrapper owns everything
around it: consumer-group subscription, exponential-backoff retry on
handler failure, and dead-letter republishing once retries are exhausted,
per docs/architecture/kafka-topics.md's delivery-semantics section.
"""

import threading
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

DEFAULT_STALL_WARNING_SECONDS = 30.0
"""How long a single ``handler(envelope)`` call may run before this
consumer starts logging a repeating WARNING that it's still in flight.

Added after a real incident: `tracking-service-jobs-discovered`'s handler
hung on one message for over 10 minutes with zero log output the whole
time — the only trace left behind was `kafka-consumer-groups.sh --describe`
showing 1 message of lag and librdkafka's own ``MAXPOLL ... leaving
group`` line once ``max.poll.interval.ms`` (600s) finally evicted it from
the consumer group. Diagnosing that after the fact required cross-
referencing librdkafka's internal ``rdkafka#consumer-N`` client id against
this process's `EventConsumer` construction order by hand. A "handler
started" line plus a repeating stall warning (both carrying `topic`/
`group_id`/`correlation_id` directly, unlike librdkafka's own log) means
the *next* occurrence shows up in the log within `DEFAULT_STALL_WARNING_SECONDS`
of it happening, identifies exactly which message and correlation chain
is stuck, and needs no offset-lag detective work to notice at all."""

DEFAULT_HANDLER_TIMEOUT_SECONDS = 300.0
"""Hard ceiling on how long a single ``handler(envelope)`` call may run
before it is treated as failed, so a stuck handler can never block this
consumer's thread (and therefore that topic-partition) forever — only up to
this many seconds per attempt, after which the normal retry/DLQ path below
takes over exactly as it does for any other exception.

Added after a second real incident, this time on ``job-matching-service``:
its handler froze on ``jobs.discovered`` for 390+ seconds and counting, with
the stall watchdog above firing every 30s and zero further log output —
confirmed via a live stack dump to be parked inside the asyncio event loop's
own ``select()`` wait (not the database, which `pg_stat_activity` showed had
no query in flight for it), almost certainly the outbound
``UserPreferencesClient`` HTTP call never resolving despite its own
component-level ``httpx`` ``timeout=10.0``. A nested call's own timeout is
only a *best-effort* bound on that one call; it cannot be trusted as a bound
on the whole handler, since the transport enforcing it shares the same
event loop that may itself be the thing failing to make progress (this
codebase's consumers must run under Windows' ``SelectorEventLoop`` — see
``scripts/run_consumers.py``'s docstring — because `psycopg`'s async driver
refuses ``ProactorEventLoop``, and Selector has its own historical rough
edges on Windows). Every component's own ``handle_*`` sync entry point is
expected to wrap its ``asyncio.run(...)`` call's coroutine in
``asyncio.wait_for(..., timeout=DEFAULT_HANDLER_TIMEOUT_SECONDS)`` — this
module cannot enforce it centrally itself, since `EventHandler` is a plain
synchronous callable from `_process_message`'s point of view, with no
running event loop of its own to attach a timeout to.

Generous (5 minutes) so a legitimately slow run (e.g. several profiles each
needing their own LLM call, `infrastructure.llm.config.LLMConfig`'s own
default ``timeout_seconds=120``/``max_attempts=3``) is not mistaken for a
hang; still finite, which is the entire point."""


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
        stall_warning_seconds: float = DEFAULT_STALL_WARNING_SECONDS,
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
        self.stall_warning_seconds = stall_warning_seconds

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

        thread_name = threading.current_thread().name
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            logger.info(
                "Handler started | %s",
                format_context(
                    topic=self.topic.value,
                    event_type=envelope.event_type.value,
                    correlation_id=envelope.correlation_id,
                    group_id=self.group_id,
                    attempt=attempt,
                    thread=thread_name,
                ),
            )
            started_at = time.monotonic()
            watchdog = self._start_stall_watchdog(envelope, attempt=attempt, thread_name=thread_name, started_at=started_at)
            try:
                self.handler(envelope)
            except Exception as exc:  # noqa: BLE001 - transport can't know the handler's exception taxonomy
                watchdog.set()
                last_exc = exc
                duration_ms = round((time.monotonic() - started_at) * 1000, 1)
                logger.warning(
                    "Handler for %s failed (attempt %d/%d) after %.1fms: %s",
                    self.topic.value,
                    attempt,
                    self.max_attempts,
                    duration_ms,
                    exc,
                )
                if attempt < self.max_attempts:
                    self._sleep(self.backoff_base_seconds * (2 ** (attempt - 1)))
                continue
            else:
                watchdog.set()
                duration_ms = round((time.monotonic() - started_at) * 1000, 1)
                logger.info(
                    "Kafka event consumed | %s",
                    format_context(
                        topic=self.topic.value,
                        event_type=envelope.event_type.value,
                        correlation_id=envelope.correlation_id,
                        group_id=self.group_id,
                        duration_ms=duration_ms,
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

    def _start_stall_watchdog(
        self, envelope: EventEnvelope, *, attempt: int, thread_name: str, started_at: float
    ) -> threading.Event:
        """Start a background thread that logs a WARNING every
        ``stall_warning_seconds`` for as long as the in-flight
        ``self.handler(envelope)`` call has not returned. See
        ``DEFAULT_STALL_WARNING_SECONDS``'s docstring for why this exists.

        Returns a ``threading.Event`` the caller must ``.set()`` as soon as
        the handler call actually returns (success or exception) — that's
        what makes the blocking ``stop_event.wait(...)`` below return
        early and end the watchdog thread instead of logging a stale
        warning for a message that already finished. The thread is a
        daemon so a missed ``.set()`` still can't block process exit.
        """
        stop_event = threading.Event()

        def _watch() -> None:
            while not stop_event.wait(self.stall_warning_seconds):
                elapsed_seconds = round(time.monotonic() - started_at, 1)
                logger.warning(
                    "Handler still running past %.0fs, possible stall | %s",
                    self.stall_warning_seconds,
                    format_context(
                        topic=self.topic.value,
                        event_type=envelope.event_type.value,
                        correlation_id=envelope.correlation_id,
                        group_id=self.group_id,
                        attempt=attempt,
                        thread=thread_name,
                        elapsed_seconds=elapsed_seconds,
                    ),
                )

        watchdog_thread = threading.Thread(
            target=_watch, daemon=True, name=f"stall-watchdog:{self.group_id}:{self.topic.value}"
        )
        watchdog_thread.start()
        return stop_event

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
    "DEFAULT_HANDLER_TIMEOUT_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "EventConsumer",
    "EventHandler",
    "propagate_correlation_id",
]
