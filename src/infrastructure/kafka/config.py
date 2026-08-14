"""Kafka client configuration.

Local-first defaults matching about_project.md's Docker Compose setup; every
value is overridable from the environment so nothing is hard-coded to a
developer machine.
"""

import os
from dataclasses import dataclass

DEFAULT_BOOTSTRAP_SERVERS = "localhost:9092"


@dataclass(frozen=True)
class KafkaConfig:
    bootstrap_servers: str = DEFAULT_BOOTSTRAP_SERVERS
    # Wait for all in-sync replicas before considering a publish durable.
    acks: str = "all"
    auto_offset_reset: str = "earliest"

    @classmethod
    def from_env(cls) -> "KafkaConfig":
        return cls(
            bootstrap_servers=os.getenv(
                "KAFKA_BOOTSTRAP_SERVERS", DEFAULT_BOOTSTRAP_SERVERS
            )
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
        }


__all__ = ["DEFAULT_BOOTSTRAP_SERVERS", "KafkaConfig"]
