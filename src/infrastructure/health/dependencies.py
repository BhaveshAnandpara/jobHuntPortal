"""FastAPI dependencies for the health-probe router.

Both dependencies exist purely as override seams: in production they resolve
to the same process-wide connection machinery the rest of the app already
uses, and the test suite replaces them with fakes so `GET /health` never
needs a live PostgreSQL or Kafka — mirroring every other component's
`api/dependencies.py`.
"""

from __future__ import annotations

from typing import Annotated, Protocol, runtime_checkable

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncEngine


@runtime_checkable
class KafkaHealthClient(Protocol):
    """The single method `infrastructure.kafka.health.check_connection` needs
    from a broker client (`confluent_kafka.Producer` satisfies it
    structurally)."""

    def list_topics(self, timeout: float) -> object: ...


def get_database_engine() -> AsyncEngine:
    """The process-wide async engine — the same one every component's
    repository session is built from.

    Imported lazily so mounting this router in api/main.py never requires a
    configured database; the engine is only resolved per request, the same
    pattern as every other component's api/dependencies.py.
    """
    from infrastructure.database import get_async_engine

    return get_async_engine()


def get_kafka_health_client() -> KafkaHealthClient | None:
    """Broker client for the Kafka probe, or `None` to defer to
    `infrastructure.kafka.health.check_connection`'s own env-configured
    default client.

    `None` is the production answer on purpose: `check_connection` already
    owns real-client construction (`KafkaConfig.from_env()` +
    `confluent_kafka.Producer`) and exposes `client=` as its documented
    injection seam, so this router builds no connection of its own. Tests
    (and any future cached-client refinement) override this dependency.
    """
    return None


DatabaseEngineDep = Annotated[AsyncEngine, Depends(get_database_engine)]
KafkaHealthClientDep = Annotated[KafkaHealthClient | None, Depends(get_kafka_health_client)]

__all__ = [
    "DatabaseEngineDep",
    "KafkaHealthClient",
    "KafkaHealthClientDep",
    "get_database_engine",
    "get_kafka_health_client",
]
