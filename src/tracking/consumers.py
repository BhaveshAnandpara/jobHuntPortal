"""Kafka consumer boundary for Tracking Service — the universal downstream
consumer. Every handler below performs the same shape of work: idempotent
upsert into `applications`, append-only insert into `application_history`,
publish `ApplicationUpdatedEvent`. See
docs/architecture/component-contracts.md#tracking-service and
docs/architecture/state-machines.md#opportunity-lifecycle-applicationstatus--applicationstatus.

Each `handle_x` below is a synchronous function
(`Callable[[EventEnvelope], None]`, matching
`infrastructure.kafka.consumer.EventHandler` exactly) because
`EventConsumer._process_message` calls `self.handler(envelope)` directly,
without awaiting — registering an `async def` there would hand back an
unawaited coroutine that never runs (see `matching/consumers.py`'s module
docstring for the same rationale). The actual logic is async (DB + Kafka
calls) and lives in the matching `_handle_x_async` function; `_run_sync` is
the one shared sliver of boilerplate every `handle_x` needs to bridge the
two, so it is written once here instead of seven times.

--------------------------------------------------------------------------
Idempotency / forward-progress rule (see this component's task brief and
kafka-topics.md's at-least-once delivery semantics)
--------------------------------------------------------------------------
Every consumer may see the same message more than once (retry, rebalance),
and — per kafka-topics.md's "no cross-topic ordering guarantee" — may see
messages for the same job_id in any order. The single mechanism used to
satisfy both is `_STATUS_RANK`: a total order over `ApplicationStatus`
reflecting each status's position in the primary lifecycle chain. A
transition is only ever applied when the incoming event's target status
has **strictly greater** rank than the application's current status:

    new_rank == current_rank  -> duplicate redelivery of an
                                  already-applied transition; no-op
    new_rank <  current_rank  -> a late/out-of-order arrival for a stage
                                  already passed; no-op, never regress
    new_rank >  current_rank  -> genuine forward progress; apply it

This is the same "smallest sufficient mechanism" choice
database-ownership.md's `job_matches.published_at` note makes for Job
Matching Service's dual-write gap — no event-id dedup table, just a
comparison already implied by the domain.

Rank assignment, in chain order:

    DISCOVERED        0
    MATCHED           10   } both are the two possible outcomes of
    IGNORED [term.]   10   } consuming one jobs.matched event (recom-
                            } mendation SHORTLIST/BORDERLINE vs IGNORE) —
                            } same "stage" reached from DISCOVERED, so
                            } same rank. A same-rank comparison is a
                            } no-op, which is exactly right for a
                            } redelivered jobs.matched event (recommend-
                            } ation cannot legitimately change across
                            } redeliveries of the same immutable
                            } JobMatch, see domain-model.md#jobmatch).
    SHORTLISTED       20
    CONTACT_SEARCH    30
    CONTACT_FOUND     40
    OUTREACH_GENERATED 50
    OUTREACH_APPROVED 60
    OUTREACH_SENT     70
    REFERRED          80
    APPLIED           90
    INTERVIEW         100
    OFFER [terminal]  110
    REJECTED [terminal] 110  } two terminal branches out of INTERVIEW;
                             } neither is ever a consumer-driven target
                             } (both are manual-only per state-machines.md)
                             } so their mutual rank tie never matters for
                             } this comparison.
    WITHDRAWN [terminal] 999  } manual-only, never a consumer target;
                              } placed above everything defensively.

This rank table is used **only** by the Kafka-consumer forward-progress
check above. It is deliberately not used by the manual
`PATCH /applications/{id}/status` API path (tracking/api/routes.py), which
validates against the explicit transition graph from state-machines.md
instead — the rank order is too coarse to express, e.g., that
`APPLIED -> INTERVIEW` is valid but `DISCOVERED -> INTERVIEW` is not
(both are "forward" by rank), or that terminal states reject every
transition regardless of rank. A useful side effect: once a manual PATCH
sets, say, APPLIED (rank 90), a subsequent automatic event lower in the
chain (e.g. `contacts.found`, rank 40) naturally no-ops instead of
overwriting the user's manual choice — exactly the behavior
state-machines.md's skippability rule implies ("every state from
SHORTLISTED through REFERRED may be skipped in favor of APPLIED").

A terminal current status additionally always short-circuits to a no-op
regardless of rank math, as a defensive belt-and-braces measure — no
currently-documented event flow would ever target a lower-or-equal-ranked
status than a terminal one anyway, but this keeps the invariant explicit
rather than relying solely on the rank table staying accurate forever.

--------------------------------------------------------------------------
Row-doesn't-exist-yet handling
--------------------------------------------------------------------------
Per kafka-topics.md: "create the Application row on whichever event
arrives first if jobs.discovered hasn't been seen yet." Every handler
below therefore uses the same create-or-update shape
(`_advance`), never assuming `jobs.discovered` was seen first.

--------------------------------------------------------------------------
Denormalized company/title backfill
--------------------------------------------------------------------------
Only `NormalizedJob` (the jobs.discovered payload) carries `company`/
`title`. If an Application row had to be created from a different event
first (out-of-order arrival), those fields start as `""` placeholders
(see tracking/models.py). When jobs.discovered eventually arrives,
`_advance` backfills them onto the existing row even though the
transition itself is a no-op by rank (DISCOVERED is always rank 0, the
lowest rank, so it can never be forward progress once any other event has
already been applied) — backfilling denormalized display fields is not a
"status update" in the sense the forward-progress rule is about, so it is
applied unconditionally whenever new non-empty values are available and
the stored ones are still blank. This is a known, documented consequence
of the architecture's no-cross-topic-ordering guarantee (see this
component's task brief and kafka-topics.md), not something to solve with
a synchronous cross-component API call (which Tracking Service is
architecturally forbidden from making — service-boundaries.md).

--------------------------------------------------------------------------
`contacts.found` empty-list judgment call
--------------------------------------------------------------------------
component-contracts.md's `contacts.found` entry says the status becomes
CONTACT_FOUND "or stays CONTACT_SEARCH if empty" and still lists
ApplicationUpdatedEvent as emitted in that entry's block, while
state-machines.md explicitly calls the empty case "not a transition —
documented no-op". Reconciled here as: when the Application already
exists at CONTACT_SEARCH (the normal, in-order case) and contacts is
empty, the target status equals the current status, so `_advance`'s own
rank check (`new_rank == current_rank`) naturally treats it as a no-op —
no history row, no publish, matching state-machines.md's explicit
language. When no Application row exists yet at all (out-of-order
arrival) and contacts is empty, `_advance` creates the row at
CONTACT_SEARCH with one history entry (`from_status=None`) and *does*
publish — this is a genuine first-observation of the application's
existence (previous_status=None -> CONTACT_SEARCH), not a "stays the
same" case, so publishing is correct and not in tension with
state-machines.md's no-op language, which was written with the
already-exists case in mind.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.kafka.consumer import propagate_correlation_id
from infrastructure.logging import format_context, get_logger
from shared.events.envelope import EventEnvelope
from shared.events.payloads import OutreachDecision, OutreachSentConfirmation
from shared.types.domain.application import Application
from shared.types.domain.application_history import ApplicationHistory
from shared.types.dto import (
    ApplicationStatusUpdate,
    ContactRankingResult,
    JobMatchResult,
    NormalizedJob,
    OutreachDraft,
)
from shared.types.enums import (
    ApplicationStatus,
    EventType,
    MatchRecommendation,
    OutreachDecisionType,
)
from shared.types.ids import (
    ApplicationHistoryId,
    ApplicationId,
    CorrelationId,
    JobId,
    UserId,
)
from tracking.db import tracking_session_scope
from tracking.events import publish_application_updated
from tracking.repository import ApplicationHistoryRepository, ApplicationRepository

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Forward-progress rank table — see module docstring.
# ---------------------------------------------------------------------------

_STATUS_RANK: dict[ApplicationStatus, int] = {
    ApplicationStatus.DISCOVERED: 0,
    ApplicationStatus.MATCHED: 10,
    ApplicationStatus.IGNORED: 10,
    ApplicationStatus.SHORTLISTED: 20,
    ApplicationStatus.CONTACT_SEARCH: 30,
    ApplicationStatus.CONTACT_FOUND: 40,
    ApplicationStatus.OUTREACH_GENERATED: 50,
    ApplicationStatus.OUTREACH_APPROVED: 60,
    ApplicationStatus.OUTREACH_SENT: 70,
    ApplicationStatus.REFERRED: 80,
    ApplicationStatus.APPLIED: 90,
    ApplicationStatus.INTERVIEW: 100,
    ApplicationStatus.OFFER: 110,
    ApplicationStatus.REJECTED: 110,
    ApplicationStatus.WITHDRAWN: 999,
}

_TERMINAL_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.OFFER,
        ApplicationStatus.REJECTED,
        ApplicationStatus.IGNORED,
        ApplicationStatus.WITHDRAWN,
    }
)


# ---------------------------------------------------------------------------
# Shared upsert-history-publish-decision helper — used by all 7 consumers.
# ---------------------------------------------------------------------------


async def _advance(
    session: AsyncSession,
    *,
    job_id: JobId,
    user_id: UserId,
    target_status: ApplicationStatus,
    triggered_by: str,
    source_event_type: EventType,
    correlation_id: CorrelationId,
    company: str | None = None,
    title: str | None = None,
    field_updates: dict[str, object] | None = None,
) -> ApplicationStatusUpdate | None:
    """Create-or-advance one `Application` for `job_id`, per this module's
    docstring. Returns the `ApplicationStatusUpdate` to publish, or `None`
    if this call was a no-op (duplicate delivery, out-of-order/regressive
    arrival, or a terminal-state guard).
    """
    app_repo = ApplicationRepository(session)
    history_repo = ApplicationHistoryRepository(session)

    application = await app_repo.get_for_job(job_id)
    now = datetime.now(UTC)

    if application is None:
        application = Application(
            id=ApplicationId(uuid4()),
            job_id=job_id,
            user_id=user_id,
            company=company or "",
            title=title or "",
            status=target_status,
            created_at=now,
            updated_at=now,
            **(field_updates or {}),
        )
        await app_repo.add(application)
        await history_repo.add(
            ApplicationHistory(
                id=ApplicationHistoryId(uuid4()),
                application_id=application.id,
                from_status=None,
                to_status=target_status,
                changed_at=now,
                triggered_by=triggered_by,
                source_event_type=source_event_type,
                correlation_id=correlation_id,
            )
        )
        logger.info(
            "Application created | %s",
            format_context(
                application_id=application.id,
                job_id=job_id,
                status=target_status.value,
                correlation_id=correlation_id,
            ),
        )
        return ApplicationStatusUpdate(
            application_id=application.id,
            job_id=job_id,
            user_id=user_id,
            previous_status=None,
            new_status=target_status,
            changed_at=now,
            triggered_by=triggered_by,
        )

    # Existing row: backfill denormalized company/title if newly available
    # and currently blank — see module docstring's "backfill" section. Not
    # itself a status transition, so it happens regardless of the
    # forward-progress outcome below.
    backfilled = False
    if company and not application.company:
        application.company = company
        backfilled = True
    if title and not application.title:
        application.title = title
        backfilled = True

    is_forward_progress = _STATUS_RANK[target_status] > _STATUS_RANK[application.status]
    if application.status in _TERMINAL_STATUSES or not is_forward_progress:
        logger.info(
            "Application status update ignored (terminal or non-forward) | %s",
            format_context(
                application_id=application.id,
                current_status=application.status.value,
                attempted_status=target_status.value,
            ),
        )
        if backfilled:
            application.updated_at = now
            await app_repo.update(application)
        return None

    previous_status = application.status
    application.status = target_status
    for field, value in (field_updates or {}).items():
        setattr(application, field, value)
    application.updated_at = now
    await app_repo.update(application)
    logger.info(
        "Application status transition | %s",
        format_context(
            application_id=application.id,
            from_status=previous_status.value,
            to_status=target_status.value,
            triggered_by=triggered_by,
            correlation_id=correlation_id,
        ),
    )
    await history_repo.add(
        ApplicationHistory(
            id=ApplicationHistoryId(uuid4()),
            application_id=application.id,
            from_status=previous_status,
            to_status=target_status,
            changed_at=now,
            triggered_by=triggered_by,
            source_event_type=source_event_type,
            correlation_id=correlation_id,
        )
    )
    return ApplicationStatusUpdate(
        application_id=application.id,
        job_id=job_id,
        user_id=user_id,
        previous_status=previous_status,
        new_status=target_status,
        changed_at=now,
        triggered_by=triggered_by,
    )


# ---------------------------------------------------------------------------
# 1. jobs.discovered
# ---------------------------------------------------------------------------


async def _handle_job_discovered_async(envelope: EventEnvelope[NormalizedJob]) -> None:
    job = envelope.payload
    correlation_id = propagate_correlation_id(envelope)
    async with tracking_session_scope() as session:
        update = await _advance(
            session,
            job_id=job.job_id,
            user_id=job.user_id,
            target_status=ApplicationStatus.DISCOVERED,
            triggered_by=envelope.metadata.producer,
            source_event_type=EventType.JOB_DISCOVERED,
            correlation_id=correlation_id,
            company=job.company,
            title=job.title,
        )
    if update is not None:
        await publish_application_updated(update, correlation_id=correlation_id)


# ---------------------------------------------------------------------------
# 2. jobs.matched
# ---------------------------------------------------------------------------


async def _handle_job_matched_async(envelope: EventEnvelope[JobMatchResult]) -> None:
    result = envelope.payload
    correlation_id = propagate_correlation_id(envelope)
    target_status = (
        ApplicationStatus.IGNORED
        if result.recommendation == MatchRecommendation.IGNORE
        else ApplicationStatus.MATCHED
    )
    async with tracking_session_scope() as session:
        update = await _advance(
            session,
            job_id=result.job_id,
            user_id=result.user_id,
            target_status=target_status,
            triggered_by=envelope.metadata.producer,
            source_event_type=EventType.JOB_MATCHED,
            correlation_id=correlation_id,
            field_updates={
                "selected_resume_id": result.selected_resume_id,
                "match_score": result.match_score,
                "matched_skills": list(result.matched_skills),
                "missing_skills": list(result.missing_skills),
            },
        )
    if update is not None:
        await publish_application_updated(update, correlation_id=correlation_id)


# ---------------------------------------------------------------------------
# 3. jobs.shortlisted — SHORTLISTED then, same handler call, CONTACT_SEARCH
# ---------------------------------------------------------------------------


async def _handle_job_shortlisted_async(envelope: EventEnvelope[JobMatchResult]) -> None:
    result = envelope.payload
    correlation_id = propagate_correlation_id(envelope)
    async with tracking_session_scope() as session:
        shortlisted_update = await _advance(
            session,
            job_id=result.job_id,
            user_id=result.user_id,
            target_status=ApplicationStatus.SHORTLISTED,
            triggered_by=envelope.metadata.producer,
            source_event_type=EventType.JOB_SHORTLISTED,
            correlation_id=correlation_id,
            field_updates={
                "selected_resume_id": result.selected_resume_id,
                "match_score": result.match_score,
                "matched_skills": list(result.matched_skills),
                "missing_skills": list(result.missing_skills),
            },
        )
        contact_search_update = await _advance(
            session,
            job_id=result.job_id,
            user_id=result.user_id,
            target_status=ApplicationStatus.CONTACT_SEARCH,
            triggered_by=envelope.metadata.producer,
            source_event_type=EventType.JOB_SHORTLISTED,
            correlation_id=correlation_id,
        )
    if shortlisted_update is not None:
        await publish_application_updated(shortlisted_update, correlation_id=correlation_id)
    if contact_search_update is not None:
        await publish_application_updated(contact_search_update, correlation_id=correlation_id)


# ---------------------------------------------------------------------------
# 4. contacts.found
# ---------------------------------------------------------------------------


async def _handle_contacts_found_async(envelope: EventEnvelope[ContactRankingResult]) -> None:
    result = envelope.payload
    correlation_id = propagate_correlation_id(envelope)
    if result.contacts:
        target_status = ApplicationStatus.CONTACT_FOUND
        field_updates: dict[str, object] | None = {
            "referral_contact_id": result.contacts[0].contact_id
        }
    else:
        # "stays CONTACT_SEARCH" — see module docstring's judgment-call
        # note. Naturally a no-op via the rank check when the row already
        # exists at CONTACT_SEARCH; a genuine creation+publish when it
        # doesn't exist yet (out-of-order arrival).
        target_status = ApplicationStatus.CONTACT_SEARCH
        field_updates = None

    async with tracking_session_scope() as session:
        update = await _advance(
            session,
            job_id=result.job_id,
            user_id=result.user_id,
            target_status=target_status,
            triggered_by=envelope.metadata.producer,
            source_event_type=EventType.CONTACTS_FOUND,
            correlation_id=correlation_id,
            field_updates=field_updates,
        )
    if update is not None:
        await publish_application_updated(update, correlation_id=correlation_id)


# ---------------------------------------------------------------------------
# 5. outreach.generated
# ---------------------------------------------------------------------------


async def _handle_outreach_generated_async(envelope: EventEnvelope[OutreachDraft]) -> None:
    draft = envelope.payload
    correlation_id = propagate_correlation_id(envelope)
    async with tracking_session_scope() as session:
        update = await _advance(
            session,
            job_id=draft.job_id,
            user_id=draft.user_id,
            target_status=ApplicationStatus.OUTREACH_GENERATED,
            triggered_by=envelope.metadata.producer,
            source_event_type=EventType.OUTREACH_GENERATED,
            correlation_id=correlation_id,
        )
    if update is not None:
        await publish_application_updated(update, correlation_id=correlation_id)


# ---------------------------------------------------------------------------
# 6. outreach.approved
# ---------------------------------------------------------------------------


async def _handle_outreach_approved_async(envelope: EventEnvelope[OutreachDecision]) -> None:
    decision = envelope.payload
    correlation_id = propagate_correlation_id(envelope)
    if decision.decision != OutreachDecisionType.APPROVED:
        # Every message on this topic carries decision == APPROVED today
        # (event-contracts.md's OutreachApprovedEvent note: the REJECTED
        # value exists on OutreachDecision only so a future revision could
        # route rejections through this same topic without a breaking
        # change). ApplicationStatus has no OUTREACH_REJECTED state, so
        # there is nothing to advance to for that case yet; no-op rather
        # than guess at a shape not yet ratified anywhere in this
        # architecture.
        logger.info(
            "outreach.approved for job %s carries decision=%s, not APPROVED; no-op",
            decision.job_id,
            decision.decision,
        )
        return
    async with tracking_session_scope() as session:
        update = await _advance(
            session,
            job_id=decision.job_id,
            user_id=decision.user_id,
            target_status=ApplicationStatus.OUTREACH_APPROVED,
            triggered_by=envelope.metadata.producer,
            source_event_type=EventType.OUTREACH_APPROVED,
            correlation_id=correlation_id,
        )
    if update is not None:
        await publish_application_updated(update, correlation_id=correlation_id)


# ---------------------------------------------------------------------------
# 7. outreach.sent
# ---------------------------------------------------------------------------


async def _handle_outreach_sent_async(envelope: EventEnvelope[OutreachSentConfirmation]) -> None:
    confirmation = envelope.payload
    correlation_id = propagate_correlation_id(envelope)
    async with tracking_session_scope() as session:
        update = await _advance(
            session,
            job_id=confirmation.job_id,
            user_id=confirmation.user_id,
            target_status=ApplicationStatus.OUTREACH_SENT,
            triggered_by=envelope.metadata.producer,
            source_event_type=EventType.OUTREACH_SENT,
            correlation_id=correlation_id,
        )
    if update is not None:
        await publish_application_updated(update, correlation_id=correlation_id)


# ---------------------------------------------------------------------------
# Sync entry points required by EventConsumer's EventHandler contract.
# ---------------------------------------------------------------------------


def _run_sync(async_handler: Callable[[EventEnvelope], Awaitable[None]], envelope: EventEnvelope) -> None:
    """The one shared sliver of boilerplate every `handle_x` below needs —
    see module docstring's opening paragraph for why this can't just be
    `async def handle_x` registered directly with `EventConsumer`.
    """
    asyncio.run(async_handler(envelope))


def handle_job_discovered(envelope: EventEnvelope[NormalizedJob]) -> None:
    """Constructible as `EventConsumer(Topic.JOBS_DISCOVERED,
    "tracking-service", handle_job_discovered, dlq_producer=...)`.
    """
    _run_sync(_handle_job_discovered_async, envelope)


def handle_job_matched(envelope: EventEnvelope[JobMatchResult]) -> None:
    _run_sync(_handle_job_matched_async, envelope)


def handle_job_shortlisted(envelope: EventEnvelope[JobMatchResult]) -> None:
    _run_sync(_handle_job_shortlisted_async, envelope)


def handle_contacts_found(envelope: EventEnvelope[ContactRankingResult]) -> None:
    _run_sync(_handle_contacts_found_async, envelope)


def handle_outreach_generated(envelope: EventEnvelope[OutreachDraft]) -> None:
    _run_sync(_handle_outreach_generated_async, envelope)


def handle_outreach_approved(envelope: EventEnvelope[OutreachDecision]) -> None:
    _run_sync(_handle_outreach_approved_async, envelope)


def handle_outreach_sent(envelope: EventEnvelope[OutreachSentConfirmation]) -> None:
    _run_sync(_handle_outreach_sent_async, envelope)


__all__ = [
    "handle_contacts_found",
    "handle_job_discovered",
    "handle_job_matched",
    "handle_job_shortlisted",
    "handle_outreach_approved",
    "handle_outreach_generated",
    "handle_outreach_sent",
]
