"""Typed event producer.

Takes a canonical payload, wraps it in ``EventEnvelope[T]``, keys it per
kafka-topics.md, and publishes. Components never build an envelope or a
partition key themselves.
"""

from types import TracebackType
from typing import Self

from pydantic import BaseModel

from infrastructure.kafka.client import Headers, ProducerClient
from infrastructure.kafka.config import KafkaConfig
from infrastructure.kafka.errors import PublishError
from infrastructure.kafka.serialization import (
    build_envelope,
    partition_key,
    serialize,
)
from infrastructure.kafka.topics import Topic
from infrastructure.logging import format_context, get_logger
from shared.events.envelope import EventEnvelope
from shared.types.ids import CorrelationId

logger = get_logger(__name__)


def _default_client(config: KafkaConfig) -> ProducerClient:
    from confluent_kafka import Producer

    return Producer(config.producer_config())


class EventProducer:
    """Publishes canonical events on the approved topics.

    ``producer_name`` is the component name recorded in
    ``EventEnvelope.metadata.producer`` (e.g. ``"job-matching-service"``).
    """

    def __init__(
        self,
        producer_name: str,
        *,
        config: KafkaConfig | None = None,
        client: ProducerClient | None = None,
    ) -> None:
        self.producer_name = producer_name
        self.config = config or KafkaConfig.from_env()
        self._client = client if client is not None else _default_client(self.config)
        logger.info(
            "Kafka producer initialized | %s",
            format_context(producer=self.producer_name, bootstrap_servers=self.config.bootstrap_servers),
        )

    def publish(
        self,
        topic: Topic,
        payload: BaseModel,
        *,
        correlation_id: CorrelationId | None = None,
        source: str | None = None,
    ) -> EventEnvelope:
        """Publish ``payload`` on ``topic`` and return the envelope sent.

        Pass the ``correlation_id`` received from an upstream event to keep a
        causal chain stitched together; omit it only when this event starts a
        new chain. The returned envelope is what a caller propagates onward.
        """
        envelope = build_envelope(
            topic,
            payload,
            producer=self.producer_name,
            correlation_id=correlation_id,
            source=source,
        )
        self.publish_raw(
            topic.value,
            serialize(envelope),
            key=partition_key(topic, payload),
        )
        logger.info(
            "Kafka event published | %s",
            format_context(
                topic=topic.value,
                event_type=envelope.event_type.value,
                correlation_id=envelope.correlation_id,
                producer=self.producer_name,
            ),
        )
        return envelope

    def publish_raw(
        self,
        topic_name: str,
        value: bytes,
        *,
        key: bytes | None = None,
        headers: Headers | None = None,
    ) -> None:
        """Publish already-serialized bytes to a topic name.

        Used for dead-letter republishing, where the destination is
        ``<topic>.dlq`` rather than a ``Topic`` member and the bytes may be an
        unparseable original message that must be preserved verbatim.
        """
        self._client.produce(topic_name, value=value, key=key, headers=headers)
        # Serve delivery callbacks without blocking.
        self._client.poll(0)

    def flush(self, timeout: float = 10.0) -> None:
        """Block until every queued message is delivered.

        Raises ``PublishError`` if messages remain after ``timeout``.
        """
        remaining = self._client.flush(timeout)
        if remaining:
            raise PublishError(
                f"{remaining} message(s) still undelivered after {timeout}s"
            )

    def close(self) -> None:
        self.flush()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


__all__ = ["EventProducer"]
