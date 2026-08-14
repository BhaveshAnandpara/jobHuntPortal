"""``EventEnvelope[T]`` construction, (de)serialization and partition keying.

Pure transport logic — no broker client is imported here, so every rule in
this module is unit-testable without Kafka running.

See docs/architecture/event-contracts.md (envelope shape, the event_type/
payload agreement rule) and docs/architecture/kafka-topics.md (partition
keys).
"""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from infrastructure.kafka.errors import (
    EventDeserializationError,
    EventValidationError,
)
from infrastructure.kafka.topics import Topic, spec_for
from shared.events.envelope import EventEnvelope, EventMetadata
from shared.types.ids import CorrelationId, EventId, UserId

EVENT_VERSION = "1.0"
"""Current event schema version — see shared-types.md#versioning-rules.

Additive payload changes keep this value; anything that changes an existing
field's meaning, type or required-ness bumps it.
"""


def new_correlation_id() -> CorrelationId:
    """Mint a correlation id at the origin of a causal chain.

    Only the component that *starts* a chain calls this. Every downstream
    producer passes the ``correlation_id`` it received through unchanged, so a
    whole job/outreach chain shares one id.
    """
    return CorrelationId(uuid4())


def build_envelope(
    topic: Topic,
    payload: BaseModel,
    *,
    producer: str,
    correlation_id: CorrelationId | None = None,
    source: str | None = None,
    retry_count: int = 0,
) -> EventEnvelope:
    """Wrap ``payload`` in the canonical envelope for ``topic``.

    ``event_type`` is derived from the topic registry and the payload type is
    validated against it, so a mismatched pair cannot be published.

    ``user_id`` is read off the payload: all ten canonical payload types carry
    ``user_id``, and duplicating it as a caller argument would only create a
    way for the two to disagree.

    A ``correlation_id`` of ``None`` means "this event starts a new chain" and
    mints a fresh id; propagating components must pass the id they received.
    """
    spec = spec_for(topic)
    if not isinstance(payload, spec.payload_type):
        raise EventValidationError(
            f"topic {topic.value!r} carries {spec.payload_type.__name__}, "
            f"got {type(payload).__name__}"
        )

    return EventEnvelope[spec.payload_type](  # type: ignore[misc]
        event_id=EventId(uuid4()),
        event_type=spec.event_type,
        event_version=EVENT_VERSION,
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or new_correlation_id(),
        user_id=UserId(payload.user_id),
        payload=payload,
        metadata=EventMetadata(
            producer=producer, retry_count=retry_count, source=source
        ),
    )


def partition_key(topic: Topic, payload: BaseModel) -> bytes:
    """The per-topic partition key from kafka-topics.md.

    Keying guarantees that every event about the same entity lands in one
    partition, which is what makes per-``job_id`` (or per-``user_id`` /
    per-``application_id``) ordering hold within a topic.
    """
    spec = spec_for(topic)
    return str(getattr(payload, spec.partition_key_field)).encode("utf-8")


def serialize(envelope: EventEnvelope) -> bytes:
    return envelope.model_dump_json().encode("utf-8")


def deserialize(topic: Topic, data: bytes) -> EventEnvelope:
    """Read ``data`` back as the ``EventEnvelope[T]`` that ``topic`` carries.

    Raises ``EventDeserializationError`` — which the consumer treats as
    permanent, never retrying — if the bytes are not a valid envelope or if
    ``event_type`` disagrees with the topic.
    """
    spec = spec_for(topic)
    try:
        envelope = EventEnvelope[spec.payload_type].model_validate_json(data)  # type: ignore[misc]
    except ValidationError as exc:
        raise EventDeserializationError(
            f"message on {topic.value!r} is not a valid "
            f"EventEnvelope[{spec.payload_type.__name__}]: {exc}"
        ) from exc

    if envelope.event_type is not spec.event_type:
        raise EventDeserializationError(
            f"message on {topic.value!r} declares event_type "
            f"{envelope.event_type.value!r}, expected {spec.event_type.value!r}"
        )
    return envelope


__all__ = [
    "EVENT_VERSION",
    "build_envelope",
    "deserialize",
    "new_correlation_id",
    "partition_key",
    "serialize",
]
