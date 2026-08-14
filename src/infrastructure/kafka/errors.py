"""Kafka infrastructure errors.

These are transport-level failures only. Business failure modes belong to the
owning component and use ``shared.errors.codes.ErrorCode``.
"""


class KafkaInfrastructureError(Exception):
    """Base class for every error raised by infrastructure/kafka."""


class EventValidationError(KafkaInfrastructureError):
    """A payload does not match the contract for the topic it targets.

    Raised at publish time so a producer cannot, for example, publish a
    ``JobMatchResult`` tagged as ``JOB_DISCOVERED``
    (docs/architecture/event-contracts.md#eventenvelopet).
    """


class EventDeserializationError(KafkaInfrastructureError):
    """A consumed message could not be read back as ``EventEnvelope[T]``.

    Permanent by nature — the bytes will not become valid on a retry, so the
    consumer routes these straight to the dead-letter topic without retrying.
    """


class PublishError(KafkaInfrastructureError):
    """The broker rejected or failed to accept a produced message."""


__all__ = [
    "EventDeserializationError",
    "EventValidationError",
    "KafkaInfrastructureError",
    "PublishError",
]
