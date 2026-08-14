"""EventEnvelope construction, event_type/payload agreement, partition keys,
serialize/deserialize round trip. See docs/architecture/event-contracts.md.
"""

import pytest

from infrastructure.kafka.errors import EventDeserializationError, EventValidationError
from infrastructure.kafka.serialization import (
    build_envelope,
    deserialize,
    new_correlation_id,
    partition_key,
    serialize,
)
from infrastructure.kafka.topics import Topic, all_specs
from shared.types.enums import EventType
from tests.infrastructure.kafka.conftest import make_normalized_job, sample_payload


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.topic.value)
def test_build_envelope_sets_matching_event_type(spec) -> None:
    payload = sample_payload(spec.topic)
    envelope = build_envelope(spec.topic, payload, producer="test-producer")
    assert envelope.event_type == spec.event_type
    assert isinstance(envelope.payload, spec.payload_type)
    assert envelope.user_id == payload.user_id
    assert envelope.metadata.producer == "test-producer"
    assert envelope.metadata.retry_count == 0
    assert envelope.metadata.failure_reason is None
    assert envelope.metadata.error_code is None


def test_build_envelope_rejects_payload_type_mismatch() -> None:
    """A JobMatchResult can't be published tagged as jobs.discovered
    (NormalizedJob) — event-contracts.md's binding rule."""
    mismatched_payload = sample_payload(Topic.JOBS_MATCHED)
    with pytest.raises(EventValidationError):
        build_envelope(Topic.JOBS_DISCOVERED, mismatched_payload, producer="test-producer")


def test_build_envelope_mints_correlation_id_when_absent() -> None:
    payload = make_normalized_job()
    envelope = build_envelope(Topic.JOBS_DISCOVERED, payload, producer="test-producer")
    assert envelope.correlation_id is not None


def test_build_envelope_propagates_supplied_correlation_id() -> None:
    payload = make_normalized_job()
    correlation_id = new_correlation_id()
    envelope = build_envelope(
        Topic.JOBS_DISCOVERED, payload, producer="test-producer", correlation_id=correlation_id
    )
    assert envelope.correlation_id == correlation_id


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.topic.value)
def test_partition_key_uses_documented_field(spec) -> None:
    payload = sample_payload(spec.topic)
    key = partition_key(spec.topic, payload)
    expected = str(getattr(payload, spec.partition_key_field)).encode("utf-8")
    assert key == expected


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.topic.value)
def test_serialize_then_deserialize_round_trips(spec) -> None:
    payload = sample_payload(spec.topic)
    envelope = build_envelope(spec.topic, payload, producer="test-producer")
    data = serialize(envelope)
    recovered = deserialize(spec.topic, data)
    assert recovered == envelope


def test_deserialize_rejects_garbage_bytes() -> None:
    with pytest.raises(EventDeserializationError):
        deserialize(Topic.JOBS_DISCOVERED, b"not json at all")


def test_deserialize_rejects_event_type_mismatch() -> None:
    """A message whose event_type disagrees with the topic it's read from
    must be rejected, not silently accepted (event-contracts.md)."""
    payload = make_normalized_job()
    envelope = build_envelope(Topic.JOBS_DISCOVERED, payload, producer="test-producer")
    # Force a mismatched event_type into an otherwise-valid envelope payload
    # for the topic it will be *read* on.
    tampered = envelope.model_copy(update={"event_type": EventType.JOB_MATCHED})
    data = tampered.model_dump_json().encode("utf-8")
    with pytest.raises(EventDeserializationError):
        deserialize(Topic.JOBS_DISCOVERED, data)
