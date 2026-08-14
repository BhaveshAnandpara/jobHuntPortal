"""Kafka consumer boundary for Contact Discovery Service.

Consumes:
    `contacts.requested` -> runs ContactDiscoveryState workflow
                             (search_contacts -> rank_contacts ->
                             persist_and_publish, in-process, no Kafka hop
                             between them)
See docs/architecture/component-contracts.md#contact-discovery-service.

`handle_contacts_requested` is a synchronous function
(`Callable[[EventEnvelope], None]`, matching
`infrastructure.kafka.consumer.EventHandler` exactly) because
`EventConsumer._process_message` calls `self.handler(envelope)` directly,
without awaiting — registering an `async def` there would hand back an
unawaited coroutine that never runs. The actual workflow logic is async and
lives in `_handle_contacts_requested_async`; the public
`handle_contacts_requested` just drives it with `asyncio.run` — identical
shape to `matching.consumers.handle_job_discovered` (see that module's
docstring).
"""

from __future__ import annotations

import asyncio
import time

from contacts.db import contacts_session_scope
from contacts.errors import ContactDiscoveryError
from contacts.repository import ContactRepository
from infrastructure.kafka.consumer import propagate_correlation_id
from infrastructure.llm import LLMProviderError
from infrastructure.logging import format_context, get_logger
from shared.errors.codes import ErrorCode
from shared.events.envelope import EventEnvelope
from shared.events.payloads import ContactSearchRequest
from shared.types.ids import CorrelationId
from workflows.langgraph.contact_discovery.context import (
    reset_contact_details,
    reset_correlation_id,
    set_contact_details,
    set_correlation_id,
)
from workflows.langgraph.contact_discovery.graph import build_graph
from workflows.langgraph.contact_discovery.state import ContactDiscoveryState

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Dependency injection seams
# ---------------------------------------------------------------------------

_graph = None


def _get_graph():
    """Lazily-compiled, process-wide graph. The compiled graph itself is
    stateless (all per-run data lives in the ContactDiscoveryState passed
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


async def _run_discovery(
    payload: ContactSearchRequest, correlation_id: CorrelationId | None
) -> ContactDiscoveryState:
    initial_state: ContactDiscoveryState = {
        "job_id": payload.job_id,
        "user_id": payload.user_id,
        "company": payload.company,
        "title": payload.title,
        "location": payload.location,
        "candidates": [],
        "ranked_contacts": [],
        "errors": [],
    }

    logger.info(
        "Contact discovery started | %s",
        format_context(job_id=payload.job_id, company=payload.company, title=payload.title),
    )
    started = time.monotonic()
    correlation_token = set_correlation_id(correlation_id)
    details_token = set_contact_details({})
    try:
        result = await _get_graph().ainvoke(initial_state)
    finally:
        reset_contact_details(details_token)
        reset_correlation_id(correlation_token)

    duration_ms = round((time.monotonic() - started) * 1000, 1)
    ranked = result.get("ranked_contacts") or []
    if ranked:
        logger.info(
            "Contact discovery completed | %s",
            format_context(job_id=payload.job_id, contacts_found=len(ranked), duration_ms=duration_ms),
        )
    else:
        logger.warning(
            "Contact discovery completed with no contacts found | %s",
            format_context(job_id=payload.job_id, company=payload.company, duration_ms=duration_ms),
        )
    return result


def handle_contacts_requested(envelope: EventEnvelope[ContactSearchRequest]) -> None:
    """Sync entry point required by `EventConsumer`'s `EventHandler`
    contract — see this module's docstring for why. Constructible as
    `EventConsumer(Topic.CONTACTS_REQUESTED, "contact-discovery-service",
    handle_contacts_requested, dlq_producer=...)`.
    """
    asyncio.run(_handle_contacts_requested_async(envelope))


async def _handle_contacts_requested_async(
    envelope: EventEnvelope[ContactSearchRequest],
) -> None:
    payload = envelope.payload
    correlation_id = propagate_correlation_id(envelope)

    # Idempotency (Kafka's at-least-once delivery — kafka-topics.md): this
    # handler may see the same contacts.requested message more than once.
    #
    # Unlike Job Matching Service's dual-write gap (job_matches.published_at
    # — database-ownership.md#job_matches's "Publish-reliability column"
    # section — needed because a *downstream command topic*
    # (contacts.requested) depended on that publish actually having
    # happened), Contact Discovery has no further side effect gated on "did
    # contacts.found actually publish": contacts.found's only consumers
    # (Outreach Service, Tracking Service) are themselves ordinary
    # at-least-once Kafka consumers that must already tolerate a duplicate
    # or replayed message. So the simplest correct mechanism (per this
    # component's task brief: "pick the simpler one and document it
    # briefly") is a single existence check, not a three-way published_at
    # check: if `contacts` rows already exist for this job_id, this job_id
    # has already been fully processed by this handler (persist and
    # publish happen back-to-back inside one node, persist_and_publish,
    # with no yield point between them where a crash could leave rows
    # committed but the event unpublished under normal operation) — skip
    # re-running discovery entirely rather than re-querying people-search
    # and re-calling the LLM. A message that is dead-lettered before rows
    # are ever persisted (e.g. the search/LLM/DB step itself failed) is not
    # a "success", so its retry naturally sees zero rows and reruns the
    # full workflow, exactly as intended.
    async with contacts_session_scope() as session:
        existing = await ContactRepository(session).list_for_job(payload.job_id)

    if existing:
        logger.info(
            "job %s already has %d persisted contact(s); skipping replayed "
            "contacts.requested",
            payload.job_id,
            len(existing),
        )
        return

    try:
        await _run_discovery(payload, correlation_id)
    except ContactDiscoveryError:
        # Already normalized (carries a duck-typed error_code) — re-raise
        # unchanged so EventConsumer's retry/DLQ mechanism sees it.
        raise
    except LLMProviderError as exc:
        raise ContactDiscoveryError(
            f"unrecoverable LLM provider error discovering contacts for job "
            f"{payload.job_id}: {exc}",
            error_code=ErrorCode.LLM_PROVIDER_ERROR,
        ) from exc
    except Exception as exc:
        raise ContactDiscoveryError(
            f"unexpected failure discovering contacts for job {payload.job_id}: {exc}"
        ) from exc


__all__ = ["handle_contacts_requested", "set_graph"]
