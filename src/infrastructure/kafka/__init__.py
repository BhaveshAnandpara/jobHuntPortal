"""Kafka Infrastructure — the event backbone. Owned by the Kafka agent
(.claude/agents/kafka-agent.md). See
docs/architecture/service-boundaries.md#kafka-infrastructure.

Owns: topic definitions (docs/architecture/kafka-topics.md), EventEnvelope
(de)serialization, retry/DLQ plumbing, consumer-group naming convention.
Depended on by every component that produces or consumes an event
(docs/architecture/ownership.md#component--kafka-topics-produced).

Must not: implement business decisions, invent new topics or payloads, or
put Kafka between every LangGraph node — that belongs to the owning
component and docs/architecture/overview.md#core-architectural-rules-from-claudemd-made-explicit
rule 1-2 respectively.
"""
