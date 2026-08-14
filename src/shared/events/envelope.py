"""Kafka event envelope. See
docs/architecture/event-contracts.md#eventenvelopet.

Every Kafka message is EventEnvelope[T] where T is one of the payload
types in shared.events.payloads (or a canonical exchange type reused as a
payload — see that module's docstring). `event_type` must match the
payload type per event-contracts.md; the Kafka producer wrapper in
infrastructure/kafka/ is responsible for validating this at publish time so
a producer can't accidentally publish e.g. a JobMatchResult tagged as
JOB_DISCOVERED.
"""

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel

from shared.errors.codes import ErrorCode
from shared.types.enums import EventType
from shared.types.ids import CorrelationId, EventId, UserId

T = TypeVar("T", bound=BaseModel)


class EventMetadata(BaseModel):
    producer: str  # component name, e.g. "job-matching-service"
    retry_count: int = 0  # incremented by the retry wrapper
    source: str | None = None  # e.g. "manual" | "automatic", where applicable
    failure_reason: str | None = None  # set only on DLQ republish
    error_code: ErrorCode | None = None  # set only on DLQ republish, when classifiable


class EventEnvelope(BaseModel, Generic[T]):
    event_id: EventId
    event_type: EventType
    event_version: str  # e.g. "1.0" — see shared-types.md#versioning-rules
    timestamp: datetime  # UTC, event creation time
    correlation_id: CorrelationId  # propagated unchanged across a causal chain
    user_id: UserId
    payload: T
    metadata: EventMetadata


__all__ = ["EventEnvelope", "EventMetadata"]
