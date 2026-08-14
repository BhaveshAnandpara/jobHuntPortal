"""Kafka connectivity check, mirroring infrastructure/database/health.py's
test shape. No live broker required — the client is injected."""

from infrastructure.kafka.health import check_connection


class _FakeHealthyClient:
    def list_topics(self, timeout: float) -> object:
        return object()


class _FakeUnhealthyClient:
    def list_topics(self, timeout: float) -> object:
        raise ConnectionError("broker unreachable")


def test_check_connection_true_when_broker_reachable() -> None:
    assert check_connection(client=_FakeHealthyClient()) is True


def test_check_connection_false_when_broker_unreachable() -> None:
    assert check_connection(client=_FakeUnhealthyClient()) is False
