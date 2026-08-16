"""Kafka client configuration.

Local-first defaults matching about_project.md's Docker Compose setup; every
value is overridable from the environment so nothing is hard-coded to a
developer machine.
"""

import os
from dataclasses import dataclass

DEFAULT_BOOTSTRAP_SERVERS = "localhost:9092"

DEFAULT_MAX_POLL_INTERVAL_MS = 600_000  # 10 minutes
"""librdkafka's own default (300_000/5min) is too tight for this codebase:
`EventConsumer.run()` (infrastructure/kafka/consumer.py) processes one
message fully — including any handler retries — before calling `poll()`
again, so a slow handler blocks polling for its entire duration. Job
Matching Service's handler calls an external LLM (Groq) once per candidate
profile; under provider-side rate limiting the SDK's own retry/backoff can
stretch a single message's processing past 5 minutes, which trips this
timeout and gets the consumer evicted mid-processing — confirmed live via
`kafka-consumer-groups.sh --describe` reporting `job-matching-service` with
zero active members while every other consumer (all fast, no external API
calls) stayed healthy. 10 minutes covers a realistic worst case (several
profiles, each individually rate-limited) with headroom; the structural
fix (decouple polling from handler execution) is a bigger change to
`EventConsumer` itself, tracked separately."""

DEFAULT_SESSION_TIMEOUT_MS = 120_000  # 2 minutes
"""librdkafka's default (45_000/45s) was directly observed firing
("SESSTMOUT ... after 45032 ms") during the same incident above. Raised
alongside `max.poll.interval.ms` as defense-in-depth against the same
failure mode, since a blocked application thread can also delay servicing
whatever keeps this session considered alive."""


@dataclass(frozen=True)
class KafkaConfig:
    bootstrap_servers: str = DEFAULT_BOOTSTRAP_SERVERS
    # Wait for all in-sync replicas before considering a publish durable.
    acks: str = "all"
    auto_offset_reset: str = "earliest"
    max_poll_interval_ms: int = DEFAULT_MAX_POLL_INTERVAL_MS
    session_timeout_ms: int = DEFAULT_SESSION_TIMEOUT_MS

    @classmethod
    def from_env(cls) -> "KafkaConfig":
        return cls(
            bootstrap_servers=os.getenv(
                "KAFKA_BOOTSTRAP_SERVERS", DEFAULT_BOOTSTRAP_SERVERS
            ),
            max_poll_interval_ms=int(
                os.getenv("KAFKA_MAX_POLL_INTERVAL_MS", DEFAULT_MAX_POLL_INTERVAL_MS)
            ),
            session_timeout_ms=int(
                os.getenv("KAFKA_SESSION_TIMEOUT_MS", DEFAULT_SESSION_TIMEOUT_MS)
            ),
        )

    def producer_config(self) -> dict[str, object]:
        return {
            "bootstrap.servers": self.bootstrap_servers,
            "acks": self.acks,
            "enable.idempotence": True,
        }

    def consumer_config(self, group_id: str) -> dict[str, object]:
        return {
            "bootstrap.servers": self.bootstrap_servers,
            "group.id": group_id,
            "auto.offset.reset": self.auto_offset_reset,
            # Offsets are committed by the consumer wrapper only after a
            # message is handled or dead-lettered, which is what makes
            # at-least-once delivery (kafka-topics.md) actually hold.
            "enable.auto.commit": False,
            "max.poll.interval.ms": self.max_poll_interval_ms,
            "session.timeout.ms": self.session_timeout_ms,
        }


__all__ = [
    "DEFAULT_BOOTSTRAP_SERVERS",
    "DEFAULT_MAX_POLL_INTERVAL_MS",
    "DEFAULT_SESSION_TIMEOUT_MS",
    "KafkaConfig",
]
