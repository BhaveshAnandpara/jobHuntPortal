"""Node functions for the Outreach Generation LangGraph workflow.
See docs/architecture/langgraph-state.md#outreachgenerationstate for the
graph shape and per-node input/output/error contract:

    select_channel -> generate_message -> persist_and_publish

Owned by Outreach Service.

Dependency injection: `generate_message` reaches a lazily-constructed,
module-level default `LLMClient`, the same lazy-singleton idiom
`workflows/langgraph/job_matching/nodes.py` and
`workflows/langgraph/contact_discovery/nodes.py` already use. `persist_and
_publish` reaches `outreach.db`'s shared session scope and `outreach.events`'
producer. Tests override via the `set_*` functions exported below rather
than patching internals directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from infrastructure.llm import LLMClient, LLMProviderError
from outreach.db import outreach_session_scope
from outreach.errors import OutreachError
from outreach.events import publish_outreach_generated
from outreach.repository import OutreachRepository
from shared.errors.codes import ErrorCode
from shared.types.domain.outreach import Outreach
from shared.types.dto import OutreachDraft
from shared.types.enums import OutreachChannel, OutreachStatus
from shared.types.ids import OutreachId
from workflows.langgraph.outreach_generation.context import (
    get_correlation_id,
    get_job_context,
)
from workflows.langgraph.outreach_generation.generation import (
    LINKEDIN_CONNECTION_REQUEST_CHAR_LIMIT,
    OutreachDraftContent,
    generate_draft_content,
)
from workflows.langgraph.outreach_generation.state import OutreachGenerationState

# ---------------------------------------------------------------------------
# Dependency injection seams
# ---------------------------------------------------------------------------

_llm_client: LLMClient | None = None


def _get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


def set_llm_client(client: LLMClient | None) -> None:
    """Test seam. Pass `None` to restore the lazily-constructed default."""
    global _llm_client
    _llm_client = client


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def select_channel(state: OutreachGenerationState) -> OutreachGenerationState:
    """Rule-based: prefer a profile_url-based channel if `contact.profile_url`
    is present, else `OutreachChannel.EMAIL` if `contact.email` is present,
    else abort. No external tools.

    Channel choice when `profile_url` is present:
    `OutreachChannel.LINKEDIN_CONNECTION_REQUEST`, not
    `LINKEDIN_MESSAGE`. Reasoning: nothing in this system's data model
    signals a prior connection between the candidate and the contact —
    `ContactStatus` (state-machines.md#contact-lifecycle) deliberately has
    no `CONTACTED` value, and outreach always represents a *first* touch. A
    direct LinkedIn message assumes an existing connection (or InMail
    entitlement) this system never verifies, whereas a connection request
    is always sendable to a stranger. Applied consistently: every
    `profile_url`-based contact gets a connection request, never a
    message, regardless of any other field.

    `contact.email`: `OutreachGenerationState.contact` is typed
    `RankedContact` (shared-types.md#contactrankingresult), which carries
    no `email` field — only the `Contact` domain entity does
    (domain-model.md#contact), and database-ownership.md#contacts limits
    Outreach Service to reading `Contact` data via `ContactRankingResult`
    only, never a direct table query. So the email branch below is
    reachable only if a future, additive `RankedContact.email` field is
    added upstream; `getattr` is used defensively (never raises) so this
    node satisfies langgraph-state.md's documented "contact.email" input
    without assuming a field that doesn't exist today. This is a real,
    flagged architecture gap — see this component's implementation
    report's Issues section — not a silent workaround.

    Aborts (raises `OutreachError`, `error_code=OUTREACH_GENERATION_FAILED`
    — the closest existing `ErrorCode`; shared-types.md's error table has
    no dedicated `UNSUPPORTED_CHANNEL` code) when the contact has neither a
    reachable `profile_url` nor `email` — no draft is possible without a
    reachable channel. Routes to `generate_message` always, otherwise (this
    abort happens via exception, before the graph's unconditional
    select_channel -> generate_message edge would matter — see graph.py).
    """
    contact = state["contact"]
    email = getattr(contact, "email", None)

    if contact.profile_url:
        channel = OutreachChannel.LINKEDIN_CONNECTION_REQUEST
    elif email:
        channel = OutreachChannel.EMAIL
    else:
        raise OutreachError(
            f"contact {contact.contact_id} has no profile_url or email; "
            f"no reachable outreach channel for job {state['job_id']}",
            error_code=ErrorCode.OUTREACH_GENERATION_FAILED,
        )

    return {**state, "channel": channel}


def _format_draft(channel: OutreachChannel, content: OutreachDraftContent) -> str:
    """Combine the LLM's structured output into the single `draft_message`
    string persisted on `Outreach`/published in `OutreachDraft`. Email
    keeps its subject line inline (there is no separate `subject` column on
    `Outreach` — domain-model.md#outreach has none); every other channel
    uses the body only. Defensively re-enforces the LinkedIn connection
    request character limit in case the model didn't fully respect the
    prompt's instruction.
    """
    if channel == OutreachChannel.EMAIL and content.subject:
        message = f"Subject: {content.subject}\n\n{content.body}"
    else:
        message = content.body

    if channel == OutreachChannel.LINKEDIN_CONNECTION_REQUEST:
        message = message[:LINKEDIN_CONNECTION_REQUEST_CHAR_LIMIT]

    return message


async def generate_message(state: OutreachGenerationState) -> OutreachGenerationState:
    """External tools: LLM Provider Layer (message generation), via
    `workflows.langgraph.outreach_generation.generation.generate_draft_content`.
    Job context (`company`/`title`) needed for personalization comes from
    `workflows.langgraph.outreach_generation.context.get_job_context()` —
    `OutreachGenerationState` has no `job` field (see that module's
    docstring for why).

    On `OUTREACH_GENERATION_FAILED`/`LLM_PROVIDER_ERROR`: aborts (raises
    `OutreachError`) — no draft is persisted or published, since a human
    cannot approve a message that doesn't exist. This is the one node in
    this workflow where failure means "stop the whole run," unlike Job
    Matching's/Contact Discovery's per-item-continue pattern.
    """
    channel = state["channel"]
    if channel is None:
        raise OutreachError(
            f"generate_message reached for job {state['job_id']} without a "
            "channel — select_channel must run first",
            error_code=ErrorCode.OUTREACH_GENERATION_FAILED,
        )

    job_context = get_job_context()
    if job_context is None:
        raise OutreachError(
            f"generate_message reached for job {state['job_id']} without "
            "job context (company/title) — the caller must set it via "
            "workflows.langgraph.outreach_generation.context.set_job_context "
            "before invoking the graph",
            error_code=ErrorCode.OUTREACH_GENERATION_FAILED,
        )

    llm_client = _get_llm_client()
    try:
        content = generate_draft_content(
            llm_client,
            channel=channel,
            contact=state["contact"],
            candidate_profile=state["candidate_profile"],
            company=job_context.company,
            title=job_context.title,
        )
    except LLMProviderError as exc:
        raise OutreachError(
            f"message generation failed for job {state['job_id']}: {exc}",
            error_code=ErrorCode.LLM_PROVIDER_ERROR,
        ) from exc

    draft_message = _format_draft(channel, content)
    return {**state, "draft_message": draft_message}


async def persist_and_publish(state: OutreachGenerationState) -> OutreachGenerationState:
    """External tools: `outreach` repository (DB write), Kafka producer.
    Terminal node. Destination: `INSERT outreach` (`status=
    PENDING_APPROVAL` — `DRAFT` is a transient in-memory
    `OutreachGenerationState` value only, per state-machines.md, never
    written to the table); publish `OutreachGeneratedEvent`. No error
    beyond standard DB/publish failure (raises `OutreachError`).
    """
    channel = state["channel"]
    draft_message = state["draft_message"]
    if channel is None or draft_message is None:
        raise OutreachError(
            f"persist_and_publish reached for job {state['job_id']} without "
            "a channel/draft_message — the graph must run select_channel "
            "and generate_message first"
        )

    contact = state["contact"]
    now = datetime.now(UTC)
    outreach = Outreach(
        id=OutreachId(uuid4()),
        job_id=state["job_id"],
        contact_id=contact.contact_id,
        user_id=state["user_id"],
        channel=channel,
        draft_message=draft_message,
        final_message=None,
        status=OutreachStatus.PENDING_APPROVAL,
        generated_at=now,
    )
    # Persistence-layer-only — never part of the `Outreach` domain type.
    # `select_channel` already established that one of these two is
    # reachable (it's how `channel` got set); persisted here so the
    # send-worker consumer (a separate, later Kafka-consumer invocation)
    # has an address to hand `MessageSendClient` without a second API
    # round-trip back to Contact Discovery Service — see
    # `outreach.models.OutreachRecord.recipient_address`'s docstring for
    # the full architecture-gap rationale.
    recipient_address = contact.profile_url or getattr(contact, "email", None)

    try:
        async with outreach_session_scope() as session:
            await OutreachRepository(session).add(outreach, recipient_address=recipient_address)
    except Exception as exc:
        raise OutreachError(
            f"failed to persist Outreach for job {state['job_id']}: {exc}"
        ) from exc

    draft = OutreachDraft(
        outreach_id=outreach.id,
        job_id=outreach.job_id,
        contact_id=outreach.contact_id,
        user_id=outreach.user_id,
        channel=outreach.channel,
        draft_message=outreach.draft_message,
        generated_at=outreach.generated_at,
    )
    try:
        await publish_outreach_generated(draft, correlation_id=get_correlation_id())
    except Exception as exc:
        raise OutreachError(
            f"failed to publish outreach.generated for job {state['job_id']}: {exc}"
        ) from exc

    return state


__all__ = [
    "generate_message",
    "persist_and_publish",
    "select_channel",
    "set_llm_client",
]
