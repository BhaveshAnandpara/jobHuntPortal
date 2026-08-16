"""Tests for KafkaConfig (config.py)."""

from __future__ import annotations

from infrastructure.kafka.config import (
    DEFAULT_BOOTSTRAP_SERVERS,
    DEFAULT_MAX_POLL_INTERVAL_MS,
    DEFAULT_SESSION_TIMEOUT_MS,
    KafkaConfig,
)


def test_defaults():
    cfg = KafkaConfig()
    assert cfg.bootstrap_servers == DEFAULT_BOOTSTRAP_SERVERS
    assert cfg.max_poll_interval_ms == DEFAULT_MAX_POLL_INTERVAL_MS
    assert cfg.session_timeout_ms == DEFAULT_SESSION_TIMEOUT_MS


def test_consumer_config_includes_poll_and_session_timeouts():
    """Regression coverage: a slow handler (e.g. Job Matching Service's
    per-profile LLM calls) must not get evicted from its consumer group
    mid-processing under librdkafka's tighter defaults — see
    infrastructure/kafka/config.py's DEFAULT_MAX_POLL_INTERVAL_MS docstring
    for the incident this fixes."""
    cfg = KafkaConfig()
    consumer_config = cfg.consumer_config("job-matching-service")
    assert consumer_config["max.poll.interval.ms"] == DEFAULT_MAX_POLL_INTERVAL_MS
    assert consumer_config["session.timeout.ms"] == DEFAULT_SESSION_TIMEOUT_MS
    assert consumer_config["group.id"] == "job-matching-service"
    assert consumer_config["enable.auto.commit"] is False


def test_from_env_reads_overrides(monkeypatch):
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka.internal:9092")
    monkeypatch.setenv("KAFKA_MAX_POLL_INTERVAL_MS", "900000")
    monkeypatch.setenv("KAFKA_SESSION_TIMEOUT_MS", "60000")

    cfg = KafkaConfig.from_env()

    assert cfg.bootstrap_servers == "kafka.internal:9092"
    assert cfg.max_poll_interval_ms == 900000
    assert cfg.session_timeout_ms == 60000


def test_from_env_uses_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    monkeypatch.delenv("KAFKA_MAX_POLL_INTERVAL_MS", raising=False)
    monkeypatch.delenv("KAFKA_SESSION_TIMEOUT_MS", raising=False)

    cfg = KafkaConfig.from_env()

    assert cfg.bootstrap_servers == DEFAULT_BOOTSTRAP_SERVERS
    assert cfg.max_poll_interval_ms == DEFAULT_MAX_POLL_INTERVAL_MS
    assert cfg.session_timeout_ms == DEFAULT_SESSION_TIMEOUT_MS


def test_producer_config_unaffected():
    cfg = KafkaConfig()
    producer_config = cfg.producer_config()
    assert "max.poll.interval.ms" not in producer_config
    assert "session.timeout.ms" not in producer_config
