"""Tests for `outreach.consumers` — the `contacts.found` consumer and the
`outreach.approved` send-worker consumer (separate consumer group).

Covers scenarios D (human approval gate), E (rejection never sends), F
(idempotent duplicate approval covered in test_api.py), G (duplicate send),
H (LLM failure), I (external send failure), J (Kafka correlation_id
propagation), K (persistence at each stage).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

import outreach.consumers as consumers_module
import outreach.db as db_module
import outreach.events as events_module
import workflows.langgraph.outreach_generation.nodes as nodes_module
from infrastructure.external.errors import MessageSendError
from infrastructure.external.message_send import (
    MessageSendClient,
    RecordingMessageSendProvider,
)
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import build_envelope, deserialize
from infrastructure.kafka.topics import Topic
from outreach.errors import OutreachError
from outreach.repository import OutreachRepository
from shared.errors.codes import ErrorCode
from shared.events.payloads import OutreachDecision
from shared.types.domain.outreach import Outreach
from shared.types.dto import ContactRankingResult
from shared.types.enums import OutreachChannel, OutreachDecisionType, OutreachStatus
from shared.types.ids import ContactId, CorrelationId, JobId, OutreachId, UserId
from tests.outreach.conftest import (
    FakeJobIngestionClient,
    FakeJobMatchClient,
    FakeLLMClient,
    FakeProfileServiceClient,
    make_job_match_response,
    make_job_response,
    make_ranked_contact,
    make_resume_profile,
)
from workflows.langgraph.outreach_generation.generation import OutreachDraftContent

# ---------------------------------------------------------------------------
# contacts.found consumer
# ---------------------------------------------------------------------------


def _make_result(job_id: JobId, user_id: UserId, contacts) -> ContactRankingResult:
    return ContactRankingResult(
        job_id=job_id, user_id=user_id, contacts=contacts, ranked_at=datetime.now(UTC)
    )


@pytest.mark.asyncio
async def test_handle_contacts_found_empty_contacts_is_a_noop(session_factory) -> None:
    job_id, user_id = JobId(uuid4()), UserId(uuid4())
    broker = InMemoryBroker()
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(broker))
    )
    envelope = build_envelope(
        Topic.CONTACTS_FOUND, _make_result(job_id, user_id, []), producer="contact-discovery-service"
    )

    await consumers_module._handle_contacts_found_async(envelope)

    assert broker.log(Topic.OUTREACH_GENERATED.value) == []


@pytest.mark.asyncio
async def test_handle_contacts_found_generates_and_publishes_with_correlation_id(
    session_factory,
) -> None:
    job_id, user_id = JobId(uuid4()), UserId(uuid4())
    contact = make_ranked_contact(profile_url="https://example.com/in/jordan-smith")
    job_match = make_job_match_response()
    profile = make_resume_profile().model_copy(update={"profile_id": job_match.selected_profile_id})
    job = make_job_response(job_id, company="Acme Robotics", title="Senior Mechanical Engineer")

    broker = InMemoryBroker()
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(broker))
    )
    consumers_module.set_job_match_client(FakeJobMatchClient({job_id: job_match}))
    consumers_module.set_profile_service_client(
        FakeProfileServiceClient({profile.profile_id: profile})
    )
    consumers_module.set_job_ingestion_client(FakeJobIngestionClient({job_id: job}))
    nodes_module.set_llm_client(FakeLLMClient(default=OutreachDraftContent(body="Hi Jordan, ...")))

    correlation_id = CorrelationId(uuid4())
    envelope = build_envelope(
        Topic.CONTACTS_FOUND,
        _make_result(job_id, user_id, [contact]),
        producer="contact-discovery-service",
        correlation_id=correlation_id,
    )

    await consumers_module._handle_contacts_found_async(envelope)

    messages = broker.log(Topic.OUTREACH_GENERATED.value)
    assert len(messages) == 1
    published = deserialize(Topic.OUTREACH_GENERATED, messages[0].value())
    assert published.correlation_id == correlation_id
    assert published.payload.job_id == job_id
    assert published.payload.channel == OutreachChannel.LINKEDIN_CONNECTION_REQUEST

    async with session_factory() as session:
        stored = await OutreachRepository(session).get_for_job(job_id)
        assert stored is not None
        assert stored.status == OutreachStatus.PENDING_APPROVAL


@pytest.mark.asyncio
async def test_handle_contacts_found_skips_replayed_message_once_outreach_exists(
    session_factory,
) -> None:
    """Idempotency: a redelivered contacts.found for a job_id that already
    has an Outreach row must not create a second draft."""
    job_id, user_id = JobId(uuid4()), UserId(uuid4())
    contact = make_ranked_contact(profile_url="https://example.com/in/jordan-smith")
    job_match = make_job_match_response()
    profile = make_resume_profile().model_copy(update={"profile_id": job_match.selected_profile_id})
    job = make_job_response(job_id)

    broker = InMemoryBroker()
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(broker))
    )
    match_client = FakeJobMatchClient({job_id: job_match})
    consumers_module.set_job_match_client(match_client)
    consumers_module.set_profile_service_client(
        FakeProfileServiceClient({profile.profile_id: profile})
    )
    consumers_module.set_job_ingestion_client(FakeJobIngestionClient({job_id: job}))
    nodes_module.set_llm_client(FakeLLMClient(default=OutreachDraftContent(body="Hi Jordan, ...")))

    envelope = build_envelope(
        Topic.CONTACTS_FOUND, _make_result(job_id, user_id, [contact]), producer="contact-discovery-service"
    )

    await consumers_module._handle_contacts_found_async(envelope)
    assert len(broker.log(Topic.OUTREACH_GENERATED.value)) == 1

    # Redeliver the identical message.
    await consumers_module._handle_contacts_found_async(envelope)

    assert len(broker.log(Topic.OUTREACH_GENERATED.value)) == 1  # no duplicate draft
    assert len(match_client.requested) == 1  # job match not re-fetched


@pytest.mark.asyncio
async def test_handle_contacts_found_missing_job_match_raises_outreach_generation_failed(
    session_factory,
) -> None:
    job_id, user_id = JobId(uuid4()), UserId(uuid4())
    contact = make_ranked_contact()
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(InMemoryBroker()))
    )
    consumers_module.set_job_match_client(FakeJobMatchClient({}))  # no match registered

    envelope = build_envelope(
        Topic.CONTACTS_FOUND, _make_result(job_id, user_id, [contact]), producer="contact-discovery-service"
    )

    with pytest.raises(OutreachError) as exc_info:
        await consumers_module._handle_contacts_found_async(envelope)
    assert exc_info.value.error_code == ErrorCode.OUTREACH_GENERATION_FAILED


@pytest.mark.asyncio
async def test_handle_contacts_found_missing_job_raises_outreach_generation_failed(
    session_factory,
) -> None:
    """The job-ingestion leg of the runtime API chain (company/title for
    personalization) fails closed the same way the job-match leg does."""
    job_id, user_id = JobId(uuid4()), UserId(uuid4())
    contact = make_ranked_contact(profile_url="https://example.com/in/jordan-smith")
    job_match = make_job_match_response()
    profile = make_resume_profile().model_copy(update={"profile_id": job_match.selected_profile_id})
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(InMemoryBroker()))
    )
    consumers_module.set_job_match_client(FakeJobMatchClient({job_id: job_match}))
    consumers_module.set_profile_service_client(
        FakeProfileServiceClient({profile.profile_id: profile})
    )
    consumers_module.set_job_ingestion_client(FakeJobIngestionClient({}))  # no job registered

    envelope = build_envelope(
        Topic.CONTACTS_FOUND, _make_result(job_id, user_id, [contact]), producer="contact-discovery-service"
    )

    with pytest.raises(OutreachError) as exc_info:
        await consumers_module._handle_contacts_found_async(envelope)
    assert exc_info.value.error_code == ErrorCode.OUTREACH_GENERATION_FAILED


@pytest.mark.asyncio
async def test_handle_contacts_found_llm_failure_raises_llm_provider_error(session_factory) -> None:
    """Scenario H: LLM failure -> LLM_PROVIDER_ERROR, no draft persisted,
    no outreach.generated published."""
    from infrastructure.llm import LLMFailureReason, LLMProviderError

    job_id, user_id = JobId(uuid4()), UserId(uuid4())
    contact = make_ranked_contact(profile_url="https://example.com/in/jordan-smith")
    job_match = make_job_match_response()
    profile = make_resume_profile().model_copy(update={"profile_id": job_match.selected_profile_id})
    job = make_job_response(job_id)

    broker = InMemoryBroker()
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(broker))
    )
    consumers_module.set_job_match_client(FakeJobMatchClient({job_id: job_match}))
    consumers_module.set_profile_service_client(
        FakeProfileServiceClient({profile.profile_id: profile})
    )
    consumers_module.set_job_ingestion_client(FakeJobIngestionClient({job_id: job}))
    error = LLMProviderError(LLMFailureReason.TIMEOUT, "timed out", provider="fake", model="fake-model")
    nodes_module.set_llm_client(FakeLLMClient(default=error))

    envelope = build_envelope(
        Topic.CONTACTS_FOUND, _make_result(job_id, user_id, [contact]), producer="contact-discovery-service"
    )

    with pytest.raises(OutreachError) as exc_info:
        await consumers_module._handle_contacts_found_async(envelope)

    assert exc_info.value.error_code == ErrorCode.LLM_PROVIDER_ERROR
    assert broker.log(Topic.OUTREACH_GENERATED.value) == []
    async with session_factory() as session:
        assert await OutreachRepository(session).get_for_job(job_id) is None


def test_handle_contacts_found_sync_wrapper_drives_the_async_body(monkeypatch) -> None:
    calls: list[object] = []

    async def fake_async_handler(envelope: object) -> None:
        calls.append(envelope)

    monkeypatch.setattr(consumers_module, "_handle_contacts_found_async", fake_async_handler)

    envelope = build_envelope(
        Topic.CONTACTS_FOUND,
        _make_result(JobId(uuid4()), UserId(uuid4()), []),
        producer="contact-discovery-service",
    )
    consumers_module.handle_contacts_found(envelope)

    assert calls == [envelope]


# ---------------------------------------------------------------------------
# outreach.approved send worker
# ---------------------------------------------------------------------------


def _outreach(**overrides: object) -> Outreach:
    fields: dict[str, object] = {
        "id": OutreachId(uuid4()),
        "job_id": JobId(uuid4()),
        "contact_id": ContactId(uuid4()),
        "user_id": UserId(uuid4()),
        "channel": OutreachChannel.LINKEDIN_CONNECTION_REQUEST,
        "draft_message": "Hi Jordan, I'd love to connect about the role.",
        "final_message": None,
        "status": OutreachStatus.APPROVED,
        "generated_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return Outreach(**fields)


async def _persist(session_factory, outreach: Outreach, recipient_address: str | None = "https://example.com/in/jordan-smith") -> None:
    async with session_factory() as session:
        await OutreachRepository(session).add(outreach, recipient_address=recipient_address)
        await session.commit()


def _decision_for(outreach: Outreach) -> OutreachDecision:
    return OutreachDecision(
        outreach_id=outreach.id,
        job_id=outreach.job_id,
        user_id=outreach.user_id,
        decision=OutreachDecisionType.APPROVED,
        final_message=outreach.final_message,
        decided_at=datetime.now(UTC),
        decided_by=outreach.user_id,
    )


@pytest.mark.asyncio
async def test_handle_outreach_approved_sends_and_publishes_outreach_sent(session_factory) -> None:
    """Scenario D (second half): approved outreach can send, produces
    outreach.sent."""
    outreach = _outreach(status=OutreachStatus.APPROVED)
    await _persist(session_factory, outreach)

    broker = InMemoryBroker()
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(broker))
    )
    provider = RecordingMessageSendProvider()
    consumers_module.set_message_send_client(
        MessageSendClient({OutreachChannel.LINKEDIN_CONNECTION_REQUEST: provider})
    )

    correlation_id = CorrelationId(uuid4())
    envelope = build_envelope(
        Topic.OUTREACH_APPROVED,
        _decision_for(outreach),
        producer="outreach-service",
        correlation_id=correlation_id,
    )

    await consumers_module._handle_outreach_approved_async(envelope)

    assert len(provider.sent) == 1
    assert provider.sent[0].recipient == "https://example.com/in/jordan-smith"

    messages = broker.log(Topic.OUTREACH_SENT.value)
    assert len(messages) == 1
    published = deserialize(Topic.OUTREACH_SENT, messages[0].value())
    assert published.correlation_id == correlation_id
    assert published.payload.outreach_id == outreach.id

    async with session_factory() as session:
        stored = await OutreachRepository(session).get(outreach.id)
    assert stored.status == OutreachStatus.SENT
    assert stored.external_message_id is not None


@pytest.mark.asyncio
async def test_pending_approval_outreach_blocks_send_even_if_send_worker_invoked_directly(
    session_factory,
) -> None:
    """Explicit trace: Generated (PENDING_APPROVAL) -> Attempted Send ->
    Blocked. Even calling the send-worker handler directly against a row
    still at PENDING_APPROVAL results in zero MessageSendClient.send()
    calls and no status change."""
    outreach = _outreach(status=OutreachStatus.PENDING_APPROVAL)
    await _persist(session_factory, outreach)

    broker = InMemoryBroker()
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(broker))
    )
    provider = RecordingMessageSendProvider()
    consumers_module.set_message_send_client(
        MessageSendClient({OutreachChannel.LINKEDIN_CONNECTION_REQUEST: provider})
    )

    envelope = build_envelope(
        Topic.OUTREACH_APPROVED, _decision_for(outreach), producer="outreach-service"
    )

    await consumers_module._handle_outreach_approved_async(envelope)

    assert provider.sent == []
    assert broker.log(Topic.OUTREACH_SENT.value) == []
    async with session_factory() as session:
        stored = await OutreachRepository(session).get(outreach.id)
    assert stored.status == OutreachStatus.PENDING_APPROVAL


@pytest.mark.asyncio
async def test_rejected_outreach_never_sends(session_factory) -> None:
    """Scenario E: rejected outreach does not send — MessageSendClient
    .send() is never called for a REJECTED row."""
    outreach = _outreach(status=OutreachStatus.REJECTED)
    await _persist(session_factory, outreach)

    broker = InMemoryBroker()
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(broker))
    )
    provider = RecordingMessageSendProvider()
    consumers_module.set_message_send_client(
        MessageSendClient({OutreachChannel.LINKEDIN_CONNECTION_REQUEST: provider})
    )

    envelope = build_envelope(
        Topic.OUTREACH_APPROVED, _decision_for(outreach), producer="outreach-service"
    )

    await consumers_module._handle_outreach_approved_async(envelope)

    assert provider.sent == []
    assert broker.log(Topic.OUTREACH_SENT.value) == []


@pytest.mark.asyncio
async def test_duplicate_outreach_approved_delivery_sends_only_once(session_factory) -> None:
    """Scenario G: two deliveries of the same outreach.approved message ->
    only one MessageSendClient.send() call, only one outreach.sent
    published — the highest-stakes idempotency case in this component."""
    outreach = _outreach(status=OutreachStatus.APPROVED)
    await _persist(session_factory, outreach)

    broker = InMemoryBroker()
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(broker))
    )
    provider = RecordingMessageSendProvider()
    consumers_module.set_message_send_client(
        MessageSendClient({OutreachChannel.LINKEDIN_CONNECTION_REQUEST: provider})
    )

    envelope = build_envelope(
        Topic.OUTREACH_APPROVED, _decision_for(outreach), producer="outreach-service"
    )

    await consumers_module._handle_outreach_approved_async(envelope)
    await consumers_module._handle_outreach_approved_async(envelope)

    assert len(provider.sent) == 1
    assert len(broker.log(Topic.OUTREACH_SENT.value)) == 1


@pytest.mark.asyncio
async def test_external_send_failure_marks_send_failed_with_error_and_no_sent_event(
    session_factory,
) -> None:
    """Scenario I: external send failure -> Outreach.status becomes
    SEND_FAILED with send_error populated, no outreach.sent published."""
    outreach = _outreach(status=OutreachStatus.APPROVED)
    await _persist(session_factory, outreach)

    broker = InMemoryBroker()
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(broker))
    )

    class FailingProvider:
        name = "failing"

        async def send(self, message, *, timeout_seconds):
            raise MessageSendError("provider unavailable", retryable=False)

    consumers_module.set_message_send_client(
        MessageSendClient(
            {OutreachChannel.LINKEDIN_CONNECTION_REQUEST: FailingProvider()},
        )
    )

    envelope = build_envelope(
        Topic.OUTREACH_APPROVED, _decision_for(outreach), producer="outreach-service"
    )

    with pytest.raises(OutreachError) as exc_info:
        await consumers_module._handle_outreach_approved_async(envelope)

    assert exc_info.value.error_code == ErrorCode.EXTERNAL_SEND_FAILED
    assert broker.log(Topic.OUTREACH_SENT.value) == []

    async with session_factory() as session:
        stored = await OutreachRepository(session).get(outreach.id)
    assert stored.status == OutreachStatus.SEND_FAILED
    assert stored.send_error is not None


@pytest.mark.asyncio
async def test_redelivery_against_already_send_failed_row_is_a_noop(session_factory) -> None:
    """SEND_FAILED is treated as terminal — a redelivered message against
    an already-SEND_FAILED row never re-attempts the send."""
    outreach = _outreach(status=OutreachStatus.SEND_FAILED, send_error="prior failure")
    await _persist(session_factory, outreach)

    broker = InMemoryBroker()
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(broker))
    )
    provider = RecordingMessageSendProvider()
    consumers_module.set_message_send_client(
        MessageSendClient({OutreachChannel.LINKEDIN_CONNECTION_REQUEST: provider})
    )

    envelope = build_envelope(
        Topic.OUTREACH_APPROVED, _decision_for(outreach), producer="outreach-service"
    )

    await consumers_module._handle_outreach_approved_async(envelope)

    assert provider.sent == []


@pytest.mark.asyncio
async def test_missing_outreach_row_raises_not_found(session_factory) -> None:
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(InMemoryBroker()))
    )
    decision = OutreachDecision(
        outreach_id=OutreachId(uuid4()),
        job_id=JobId(uuid4()),
        user_id=UserId(uuid4()),
        decision=OutreachDecisionType.APPROVED,
        final_message=None,
        decided_at=datetime.now(UTC),
        decided_by=UserId(uuid4()),
    )
    envelope = build_envelope(Topic.OUTREACH_APPROVED, decision, producer="outreach-service")

    with pytest.raises(OutreachError) as exc_info:
        await consumers_module._handle_outreach_approved_async(envelope)
    assert exc_info.value.error_code == ErrorCode.NOT_FOUND


@pytest.mark.asyncio
async def test_non_approved_decision_is_a_noop(session_factory) -> None:
    outreach = _outreach(status=OutreachStatus.APPROVED)
    await _persist(session_factory, outreach)

    broker = InMemoryBroker()
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(broker))
    )
    provider = RecordingMessageSendProvider()
    consumers_module.set_message_send_client(
        MessageSendClient({OutreachChannel.LINKEDIN_CONNECTION_REQUEST: provider})
    )

    decision = _decision_for(outreach).model_copy(update={"decision": OutreachDecisionType.REJECTED})
    envelope = build_envelope(Topic.OUTREACH_APPROVED, decision, producer="outreach-service")

    await consumers_module._handle_outreach_approved_async(envelope)

    assert provider.sent == []


def test_handle_outreach_approved_sync_wrapper_drives_the_async_body(monkeypatch) -> None:
    calls: list[object] = []

    async def fake_async_handler(envelope: object) -> None:
        calls.append(envelope)

    monkeypatch.setattr(consumers_module, "_handle_outreach_approved_async", fake_async_handler)

    decision = OutreachDecision(
        outreach_id=OutreachId(uuid4()),
        job_id=JobId(uuid4()),
        user_id=UserId(uuid4()),
        decision=OutreachDecisionType.APPROVED,
        final_message=None,
        decided_at=datetime.now(UTC),
        decided_by=UserId(uuid4()),
    )
    envelope = build_envelope(Topic.OUTREACH_APPROVED, decision, producer="outreach-service")
    consumers_module.handle_outreach_approved(envelope)

    assert calls == [envelope]
