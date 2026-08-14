"""Kafka consumer boundary for Outreach Service.

Consumes:
    `contacts.found`     -> for the top-ranked contact, runs
                             OutreachGenerationState workflow
                             (select_channel -> generate_message ->
                             persist_and_publish)
    `outreach.approved`  -> dedicated send-worker consumer group
                             ("outreach-service-send-worker"), decoupled
                             from the approval API request; sends via the
                             provider matching Outreach.channel
See docs/architecture/component-contracts.md#outreach-service.

Both `handle_contacts_found` and `handle_outreach_approved` are synchronous
functions (`Callable[[EventEnvelope], None]`, matching
`infrastructure.kafka.consumer.EventHandler` exactly) because
`EventConsumer._process_message` calls `self.handler(envelope)` directly,
without awaiting — registering an `async def` there would hand back an
unawaited coroutine that never runs (same rationale as
`matching.consumers`/`contacts.consumers`). The actual workflow/send logic
is async and lives in the two `_handle_*_async` functions; the public
`handle_*` functions just drive them with `asyncio.run`.

Constructible as:

    EventConsumer(Topic.CONTACTS_FOUND, "outreach-service",
                  handle_contacts_found, dlq_producer=...)
    EventConsumer(Topic.OUTREACH_APPROVED, "outreach-service-send-worker",
                  handle_outreach_approved, dlq_producer=...)

The two distinct `group_id`s are the point: a redelivery/rebalance on one
consumer group never affects the other's offsets (kafka-topics.md's
"Multiple consumer groups: yes (send-worker, Tracking)" note for
`outreach.approved`).
"""

from __future__ import annotations

import asyncio
import time

from infrastructure.external.errors import MessageSendError
from infrastructure.external.message_send import (
    MessageSendClient,
    OutboundMessage,
    RecordingMessageSendProvider,
)
from infrastructure.kafka.consumer import propagate_correlation_id
from infrastructure.llm import LLMProviderError
from infrastructure.logging import format_context, get_logger
from outreach.clients import JobIngestionClient, JobMatchClient, ProfileServiceClient
from outreach.db import outreach_session_scope
from outreach.errors import OutreachError
from outreach.events import publish_outreach_sent
from outreach.repository import OutreachRepository
from shared.errors.codes import ErrorCode
from shared.events.envelope import EventEnvelope
from shared.events.payloads import OutreachDecision, OutreachSentConfirmation
from shared.types.dto import ContactRankingResult
from shared.types.enums import OutreachChannel, OutreachDecisionType, OutreachStatus
from workflows.langgraph.outreach_generation.context import (
    JobContext,
    reset_correlation_id,
    reset_job_context,
    set_correlation_id,
    set_job_context,
)
from workflows.langgraph.outreach_generation.graph import build_graph
from workflows.langgraph.outreach_generation.state import OutreachGenerationState

logger = get_logger(__name__)

SEND_WORKER_GROUP_ID = "outreach-service-send-worker"
"""Distinct consumer group name for the `outreach.approved` send worker —
see kafka-topics.md's `outreach.approved` entry and this component's task
brief: "a separate, dedicated send-worker consumer group ... distinct from
whatever group name, if any, you'd use elsewhere"."""

# ---------------------------------------------------------------------------
# Dependency injection seams
# ---------------------------------------------------------------------------

_graph = None
_job_match_client: JobMatchClient | None = None
_profile_service_client: ProfileServiceClient | None = None
_job_ingestion_client: JobIngestionClient | None = None
_message_send_client: MessageSendClient | None = None


def _get_graph():
    """Lazily-compiled, process-wide graph. The compiled graph itself is
    stateless (all per-run data lives in the OutreachGenerationState passed
    to `ainvoke`), so one compiled instance is safe to reuse across
    messages.
    """
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def set_graph(graph: object | None) -> None:
    """Test seam — inject a pre-compiled graph. Pass `None` to restore the
    lazily-built default."""
    global _graph
    _graph = graph


def _get_job_match_client() -> JobMatchClient:
    global _job_match_client
    if _job_match_client is None:
        _job_match_client = JobMatchClient()
    return _job_match_client


def set_job_match_client(client: JobMatchClient | None) -> None:
    """Test seam. Pass `None` to restore the lazily-constructed default."""
    global _job_match_client
    _job_match_client = client


def _get_profile_service_client() -> ProfileServiceClient:
    global _profile_service_client
    if _profile_service_client is None:
        _profile_service_client = ProfileServiceClient()
    return _profile_service_client


def set_profile_service_client(client: ProfileServiceClient | None) -> None:
    """Test seam. Pass `None` to restore the lazily-constructed default."""
    global _profile_service_client
    _profile_service_client = client


def _get_job_ingestion_client() -> JobIngestionClient:
    global _job_ingestion_client
    if _job_ingestion_client is None:
        _job_ingestion_client = JobIngestionClient()
    return _job_ingestion_client


def set_job_ingestion_client(client: JobIngestionClient | None) -> None:
    """Test seam. Pass `None` to restore the lazily-constructed default."""
    global _job_ingestion_client
    _job_ingestion_client = client


def _get_message_send_client() -> MessageSendClient:
    global _message_send_client
    if _message_send_client is None:
        # Local-runnable, safe-by-default provider (no real recipient ever
        # reached) for every channel — production wiring injects a real
        # email/LinkedIn provider mapping via set_message_send_client.
        provider = RecordingMessageSendProvider()
        _message_send_client = MessageSendClient(
            {
                OutreachChannel.EMAIL: provider,
                OutreachChannel.LINKEDIN_MESSAGE: provider,
                OutreachChannel.LINKEDIN_CONNECTION_REQUEST: provider,
                OutreachChannel.OTHER: provider,
            }
        )
    return _message_send_client


def set_message_send_client(client: MessageSendClient | None) -> None:
    """Test seam. Pass `None` to restore the lazily-constructed default."""
    global _message_send_client
    _message_send_client = client


# ---------------------------------------------------------------------------
# contacts.found -> outreach generation
# ---------------------------------------------------------------------------


async def _handle_contacts_found_async(envelope: EventEnvelope[ContactRankingResult]) -> None:
    result = envelope.payload
    correlation_id = propagate_correlation_id(envelope)

    # Validation (component-contracts.md#outreach-service): an empty
    # contacts list means no outreach is generated — this consumer no-ops.
    # Tracking Service already handles this on its own side (Application
    # stays at CONTACT_SEARCH); nothing further to do here.
    if not result.contacts:
        logger.info(
            "job %s contacts.found carries an empty contacts list; no-op",
            result.job_id,
        )
        return

    # Idempotency (Kafka's at-least-once delivery — kafka-topics.md): this
    # handler may see the same contacts.found message more than once. Same
    # shape as Contact Discovery's own approach
    # (contacts.consumers._handle_contacts_requested_async's documented
    # existence-check idempotency, chosen for the identical reason:
    # persist and publish happen back-to-back inside one node with no yield
    # point between them under normal operation, so an existing row is
    # proof this job_id was already fully handled) — a redelivered
    # contacts.found for a job_id that already has an Outreach row must not
    # create a second draft.
    async with outreach_session_scope() as session:
        existing = await OutreachRepository(session).get_for_job(result.job_id)
    if existing is not None:
        logger.info(
            "job %s already has an Outreach row (%s); skipping replayed "
            "contacts.found",
            result.job_id,
            existing.id,
        )
        return

    # ContactRankingResult.contacts is already ordered descending by
    # relevance_score (shared-types.md#contactrankingresult) — the
    # top-ranked contact is simply contacts[0].
    top_contact = result.contacts[0]

    # Two-hop runtime API chain (dependency-graph.md#2-runtime-api-dependencies,
    # the "Outreach Service --> Job Matching Service" edge added for this
    # wave): GET /jobs/{job_id}/matches -> selected_profile_id/
    # selected_resume_id, then GET /profiles/{profile_id} -> ResumeProfile.
    # Any failure here (network, missing match/profile) is normalized to
    # OUTREACH_GENERATION_FAILED and raised — never silently skipped or
    # half-generated, per this component's task brief.
    match_client = _get_job_match_client()
    try:
        job_match = await match_client.get_match(result.job_id)
    except Exception as exc:
        raise OutreachError(
            f"failed to fetch JobMatch for job {result.job_id}: {exc}"
        ) from exc
    if job_match is None:
        raise OutreachError(
            f"no JobMatch found for job {result.job_id}; cannot select a "
            "resume/profile for outreach generation",
            error_code=ErrorCode.OUTREACH_GENERATION_FAILED,
        )

    profile_client = _get_profile_service_client()
    try:
        profile = await profile_client.get_profile(job_match.selected_profile_id)
    except Exception as exc:
        raise OutreachError(
            f"failed to fetch profile {job_match.selected_profile_id} for "
            f"job {result.job_id}: {exc}"
        ) from exc
    if profile is None:
        raise OutreachError(
            f"profile {job_match.selected_profile_id} not found for job "
            f"{result.job_id}",
            error_code=ErrorCode.OUTREACH_GENERATION_FAILED,
        )

    initial_state: OutreachGenerationState = {
        "job_id": result.job_id,
        "user_id": result.user_id,
        "contact": top_contact,
        "candidate_profile": profile,
        "selected_resume_id": job_match.selected_resume_id,
        "channel": None,
        "draft_message": None,
        "errors": [],
    }

    # Third leg of the runtime API chain (dependency-graph.md's
    # "Outreach Service --> Job Ingestion Service" edge): GET /jobs/{job_id}
    # -> company/title, for LLM personalization context. Neither
    # ContactRankingResult nor JobMatchResponse carries these — only the
    # jobs table (via Job Ingestion Service's read API) does. Same
    # normalize-then-raise handling as the two calls above.
    job_ingestion_client = _get_job_ingestion_client()
    try:
        job = await job_ingestion_client.get_job(result.job_id)
    except Exception as exc:
        raise OutreachError(
            f"failed to fetch job {result.job_id} for outreach personalization: {exc}"
        ) from exc
    if job is None:
        raise OutreachError(
            f"job {result.job_id} not found; cannot personalize outreach",
            error_code=ErrorCode.OUTREACH_GENERATION_FAILED,
        )

    job_context = JobContext(company=job.company, title=job.title)

    logger.info("Outreach generation started | %s", format_context(job_id=result.job_id))
    started = time.monotonic()
    correlation_token = set_correlation_id(correlation_id)
    job_context_token = set_job_context(job_context)
    try:
        final_state = await _get_graph().ainvoke(initial_state)
    except OutreachError:
        # Already normalized (carries a duck-typed error_code) — re-raise
        # unchanged so EventConsumer's retry/DLQ mechanism sees it.
        logger.exception("Outreach generation failed | %s", format_context(job_id=result.job_id))
        raise
    except LLMProviderError as exc:
        logger.exception(
            "Outreach generation failed | %s",
            format_context(job_id=result.job_id, reason="llm_provider_error"),
        )
        raise OutreachError(
            f"unrecoverable LLM provider error generating outreach for job "
            f"{result.job_id}: {exc}",
            error_code=ErrorCode.LLM_PROVIDER_ERROR,
        ) from exc
    except Exception as exc:
        logger.exception("Outreach generation failed | %s", format_context(job_id=result.job_id, reason="unexpected"))
        raise OutreachError(
            f"unexpected failure generating outreach for job {result.job_id}: {exc}"
        ) from exc
    finally:
        reset_job_context(job_context_token)
        reset_correlation_id(correlation_token)

    duration_ms = round((time.monotonic() - started) * 1000, 1)
    channel = final_state.get("channel")
    # Never log the draft message itself — see this change's security
    # requirements. Only the channel and a length are logged, never content.
    draft = final_state.get("draft_message")
    logger.info(
        "Outreach draft created | %s",
        format_context(
            job_id=result.job_id,
            channel=channel.value if channel else None,
            draft_length=len(draft) if draft else None,
            duration_ms=duration_ms,
        ),
    )


def handle_contacts_found(envelope: EventEnvelope[ContactRankingResult]) -> None:
    """Sync entry point required by `EventConsumer`'s `EventHandler`
    contract — see this module's docstring."""
    asyncio.run(_handle_contacts_found_async(envelope))


# ---------------------------------------------------------------------------
# outreach.approved -> send worker
# ---------------------------------------------------------------------------


def _split_subject(channel: OutreachChannel, message: str) -> tuple[str, str | None]:
    """Reverse `workflows.langgraph.outreach_generation.nodes._format_draft`'s
    email formatting (`"Subject: {subject}\\n\\n{body}"`) so the send
    worker can hand `MessageSendClient` a separate `subject`/`body` for
    `OutreachChannel.EMAIL`. Every other channel has no subject line.
    """
    if channel == OutreachChannel.EMAIL and message.startswith("Subject: "):
        first_line, separator, rest = message.partition("\n\n")
        if separator and first_line.startswith("Subject: "):
            return rest, first_line[len("Subject: ") :]
    return message, None


async def _handle_outreach_approved_async(envelope: EventEnvelope[OutreachDecision]) -> None:
    decision = envelope.payload
    correlation_id = propagate_correlation_id(envelope)

    if decision.decision != OutreachDecisionType.APPROVED:
        # Every message on this topic carries decision == APPROVED today
        # (event-contracts.md's OutreachApprovedEvent note) — checked
        # anyway rather than assumed, same defensive pattern Tracking
        # Service's own handle_outreach_approved uses.
        logger.info(
            "outreach.approved for outreach %s carries decision=%s, not "
            "APPROVED; send worker no-op",
            decision.outreach_id,
            decision.decision,
        )
        return

    async with outreach_session_scope() as session:
        repository = OutreachRepository(session)
        outreach = await repository.get(decision.outreach_id)
        recipient_address = (
            await repository.get_recipient_address(decision.outreach_id)
            if outreach is not None
            else None
        )

    if outreach is None:
        raise OutreachError(
            f"no Outreach row found for outreach_id {decision.outreach_id}; "
            "cannot send",
            error_code=ErrorCode.NOT_FOUND,
        )

    # Idempotency — the highest-stakes case in this whole component
    # (sending a real message twice to a real person is a genuine product
    # failure). A second delivery of the identical outreach.approved
    # message, or any redelivery against a row already permanently
    # resolved, must never call MessageSendClient.send() again.
    # SEND_FAILED is treated as terminal here too (state-machines.md
    # documents it as requiring "operator/user intervention ... out of
    # current scope", not an automatic retry loop) — this component's task
    # brief resolves the "your call" explicitly in favor of no-op.
    if outreach.status in (OutreachStatus.SENT, OutreachStatus.SEND_FAILED):
        logger.info(
            "outreach %s already %s; skipping duplicate send worker "
            "invocation (idempotency)",
            outreach.id,
            outreach.status.value,
        )
        return

    if outreach.status != OutreachStatus.APPROVED:
        # Defensive: the only documented path onto outreach.approved is the
        # /approve handler, which only runs on PENDING_APPROVAL/EDITED rows
        # and always leaves the row at APPROVED
        # (state-machines.md#outreach-lifecycle) — this guards against an
        # out-of-order/unexpected status without crashing.
        logger.info(
            "outreach %s status is %s, not APPROVED; send worker no-op",
            outreach.id,
            outreach.status.value,
        )
        return

    message_body, subject = _split_subject(
        outreach.channel, outreach.final_message or outreach.draft_message
    )
    outbound = OutboundMessage(
        channel=outreach.channel,
        recipient=recipient_address or "",
        body=message_body,
        subject=subject,
    )

    logger.info(
        "Outreach send dispatched | %s",
        format_context(outreach_id=outreach.id, channel=outreach.channel.value),
    )
    client = _get_message_send_client()
    try:
        receipt = await client.send(outbound)
    except MessageSendError as exc:
        # EXTERNAL_SEND_FAILED: recorded directly on the outreach table in
        # this handler's own exception path (not deferred solely to the
        # DLQ, which knows nothing about this component's table — per this
        # component's task brief). Once SEND_FAILED is written, the
        # idempotency check above makes any further redelivery of this
        # message a no-op rather than a retry loop, matching
        # state-machines.md's terminal-state contract for SEND_FAILED.
        logger.error(
            "Outreach send failed | %s",
            format_context(outreach_id=outreach.id, channel=outreach.channel.value, error=str(exc)),
        )
        async with outreach_session_scope() as session:
            failed = outreach.model_copy(
                update={"status": OutreachStatus.SEND_FAILED, "send_error": str(exc)}
            )
            await OutreachRepository(session).update(failed)
        raise OutreachError(
            f"send failed for outreach {outreach.id}: {exc}",
            error_code=ErrorCode.EXTERNAL_SEND_FAILED,
        ) from exc

    sent = outreach.model_copy(
        update={
            "status": OutreachStatus.SENT,
            "sent_at": receipt.sent_at,
            "external_message_id": receipt.external_message_id,
        }
    )
    async with outreach_session_scope() as session:
        await OutreachRepository(session).update(sent)

    logger.info(
        "Outreach send completed | %s",
        format_context(outreach_id=sent.id, channel=sent.channel.value, sent_at=sent.sent_at),
    )
    confirmation = OutreachSentConfirmation(
        outreach_id=sent.id,
        job_id=sent.job_id,
        user_id=sent.user_id,
        channel=sent.channel,
        sent_at=sent.sent_at,
        external_message_id=sent.external_message_id,
    )
    try:
        await publish_outreach_sent(confirmation, correlation_id=correlation_id)
    except Exception as exc:
        raise OutreachError(
            f"failed to publish outreach.sent for outreach {sent.id}: {exc}"
        ) from exc


def handle_outreach_approved(envelope: EventEnvelope[OutreachDecision]) -> None:
    """Sync entry point required by `EventConsumer`'s `EventHandler`
    contract — see this module's docstring. Constructible with a dedicated
    `group_id=SEND_WORKER_GROUP_ID` consumer group, separate from any
    other consumer group this component might use.
    """
    asyncio.run(_handle_outreach_approved_async(envelope))


__all__ = [
    "SEND_WORKER_GROUP_ID",
    "handle_contacts_found",
    "handle_outreach_approved",
    "set_graph",
    "set_job_ingestion_client",
    "set_job_match_client",
    "set_message_send_client",
    "set_profile_service_client",
]
