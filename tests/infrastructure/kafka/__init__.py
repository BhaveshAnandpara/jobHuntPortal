"""Tests for infrastructure/kafka/ (docs/architecture/kafka-topics.md,
docs/architecture/event-contracts.md). No live broker required — every test
uses infrastructure.kafka.in_memory's fake ``ProducerClient``/
``ConsumerClient``.
"""
