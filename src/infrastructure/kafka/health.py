"""Kafka connectivity check for startup probes and health endpoints.

Mirrors the shape of infrastructure/database/health.py's check_connection:
a boolean-returning function safe to call from a readiness probe, with the
broker client injectable so it never needs a live cluster in tests.
"""

from __future__ import annotations

from infrastructure.kafka.config import KafkaConfig
from infrastructure.logging import get_logger

logger = get_logger(__name__)


class _ListsTopics:
    """The subset of ``confluent_kafka.Producer`` this check needs."""

    def list_topics(self, timeout: float) -> object: ...


def _default_client(config: KafkaConfig) -> _ListsTopics:
    from confluent_kafka import Producer

    return Producer(config.producer_config())


def check_connection(
    config: KafkaConfig | None = None,
    *,
    timeout: float = 5.0,
    client: _ListsTopics | None = None,
) -> bool:
    """Return whether the configured Kafka bootstrap servers are reachable.

    Fetches cluster metadata (``list_topics``) rather than publishing —
    a metadata request is enough to prove connectivity without producing a
    real message as a side effect of a health check.
    """
    target = client if client is not None else _default_client(config or KafkaConfig.from_env())
    try:
        target.list_topics(timeout=timeout)
    except Exception:
        logger.exception("Kafka connectivity check failed")
        return False
    return True


__all__ = ["check_connection"]
