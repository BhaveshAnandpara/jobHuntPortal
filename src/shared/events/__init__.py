"""Kafka event envelope and payload types. See
docs/architecture/event-contracts.md and docs/architecture/kafka-topics.md.

Components never publish or consume a bare dict — the Kafka producer/
consumer wrapper (in infrastructure/kafka/) serializes/deserializes
directly to/from EventEnvelope[T].
"""
