"""API tests for the public `GET /health` probe (infrastructure/health/api.py).

Both dependencies are overridden with fakes — no live PostgreSQL or Kafka
broker is contacted. The engine fake is the same `MagicMock`/`AsyncMock`
shape `tests/infrastructure/database/test_health.py` uses for
`check_connection_async`, and the broker fakes mirror
`tests/infrastructure/kafka/test_health.py`'s injected clients; the
router-level wiring follows `tests/contacts/test_api.py`'s standalone-app +
`dependency_overrides` pattern.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from infrastructure.health.api import router
from infrastructure.health.dependencies import (
    get_database_engine,
    get_kafka_health_client,
)


def _healthy_engine() -> MagicMock:
    connection = AsyncMock()
    engine = MagicMock()
    engine.connect.return_value.__aenter__.return_value = connection
    engine.connect.return_value.__aexit__.return_value = False
    return engine


def _unhealthy_engine() -> MagicMock:
    engine = MagicMock()
    engine.connect.side_effect = OperationalError("SELECT 1", {}, Exception("db down"))
    return engine


class _HealthyKafkaClient:
    def list_topics(self, timeout: float) -> object:
        return object()


class _UnhealthyKafkaClient:
    def list_topics(self, timeout: float) -> object:
        raise ConnectionError("broker unreachable")


def _client(engine: object, kafka_client: object) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database_engine] = lambda: engine
    app.dependency_overrides[get_kafka_health_client] = lambda: kafka_client
    return TestClient(app)


def test_health_returns_200_when_database_and_kafka_are_up() -> None:
    client = _client(_healthy_engine(), _HealthyKafkaClient())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": True, "kafka": True}


def test_health_returns_503_when_database_is_down() -> None:
    client = _client(_unhealthy_engine(), _HealthyKafkaClient())

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "database": False, "kafka": True}


def test_health_returns_503_when_kafka_is_down() -> None:
    client = _client(_healthy_engine(), _UnhealthyKafkaClient())

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "database": True, "kafka": False}


def test_health_returns_503_when_both_dependencies_are_down() -> None:
    client = _client(_unhealthy_engine(), _UnhealthyKafkaClient())

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "database": False, "kafka": False}


def test_health_requires_no_authentication() -> None:
    """A reverse proxy / uptime checker has no token — the route must never
    answer 401/403 (see the Caddyfile and docker-compose.app.yml, which
    assume `/health` is public)."""
    client = _client(_healthy_engine(), _HealthyKafkaClient())

    response = client.get("/health")  # no Authorization header

    assert response.status_code == 200


def test_health_route_is_mounted_in_the_application() -> None:
    """Guards the api/main.py wiring, not just the standalone router.

    Read off the OpenAPI schema rather than `app.routes`: this FastAPI
    version keeps included routers lazily wrapped, so `app.routes` does not
    list a mounted router's paths.
    """
    from api.main import app

    assert "/health" in app.openapi()["paths"]
