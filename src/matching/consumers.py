"""Kafka consumer boundary for Job Matching Service.

Consumes:
    `jobs.discovered`   -> runs JobMatchingState workflow for the job
    `profiles.updated`  -> DEFERRED this increment, see handle_profile_updated
See docs/architecture/component-contracts.md#job-matching-service and
docs/architecture/kafka-topics.md.

`handle_job_discovered` is a synchronous function
(`Callable[[EventEnvelope], None]`, matching
`infrastructure.kafka.consumer.EventHandler` exactly) because
`EventConsumer._process_message` calls `self.handler(envelope)` directly,
without awaiting — registering an `async def` there would hand back an
unawaited coroutine that never runs. The actual workflow logic is async
(HTTP client, LLM, DB, and Kafka calls all use async APIs throughout this
component) and lives in `_handle_job_discovered_async`; the public
`handle_job_discovered` just drives it with `asyncio.run`.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from uuid import uuid4

from infrastructure.kafka.consumer import propagate_correlation_id
from infrastructure.llm import LLMProviderError
from infrastructure.logging import format_context, get_logger
from matching.clients import UserPreferencesClient
from matching.db import matching_session_scope
from matching.errors import MatchingError
from matching.repository import JobMatchRepository
from shared.errors.codes import ErrorCode
from shared.events.envelope import EventEnvelope
from shared.events.payloads import ProfileUpdateSummary
from shared.types.domain.user_preferences import UserPreferences
from shared.types.dto import NormalizedJob
from shared.types.ids import CorrelationId, UserId, UserPreferencesId
from workflows.langgraph.job_matching.context import (
    reset_correlation_id,
    set_correlation_id,
)
from workflows.langgraph.job_matching.graph import build_graph
from workflows.langgraph.job_matching.nodes import publish_match_result
from workflows.langgraph.job_matching.state import JobMatchingState

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Dependency injection seams
# ---------------------------------------------------------------------------

_graph = None
_user_preferences_client: UserPreferencesClient | None = None


def _get_graph():
    """Lazily-compiled, process-wide graph. The compiled graph itself is
    stateless (all per-run data lives in the JobMatchingState passed to
    `ainvoke`), so one compiled instance is safe to reuse across messages.
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


def _get_user_preferences_client() -> UserPreferencesClient:
    global _user_preferences_client
    if _user_preferences_client is None:
        _user_preferences_client = UserPreferencesClient()
    return _user_preferences_client


def set_user_preferences_client(client: UserPreferencesClient | None) -> None:
    """Test seam. Pass `None` to restore the lazily-constructed default."""
    global _user_preferences_client
    _user_preferences_client = client


def _empty_preferences(user_id: UserId) -> UserPreferences:
    """Default `UserPreferences` used when the user has none configured yet
    (`GET /users/{id}/preferences` returns 404 -> `get_preferences` returns
    `None`). Matching must not fail just because a user hasn't set search
    preferences yet (`UserPreferences` is a required `JobMatchingState`
    field, not an optional one) — every list field is empty and
    `remote_preference` is `None`, so `score_profile`'s
    `location_preference_fit` factor is scored neutrally rather than
    penalizing the candidate for a preference they never stated.
    """
    return UserPreferences(
        id=UserPreferencesId(uuid4()),
        user_id=user_id,
        updated_at=datetime.now(UTC),
    )


async def _run_matching(
    job: NormalizedJob, correlation_id: CorrelationId | None
) -> JobMatchingState:
    preferences_client = _get_user_preferences_client()
    preferences = await preferences_client.get_preferences(job.user_id)
    if preferences is None:
        preferences = _empty_preferences(job.user_id)

    initial_state: JobMatchingState = {
        "job": job,
        "profiles": [],
        "preferences": preferences,
        "profile_scores": [],
        "selected_profile": None,
        "selected_resume_id": None,
        "recommendation": None,
        "final_match": None,
        "errors": [],
    }

    logger.info("Matching started | %s", format_context(job_id=job.job_id, correlation_id=correlation_id))
    started = time.monotonic()
    token = set_correlation_id(correlation_id)
    try:
        result = await _get_graph().ainvoke(initial_state)
    finally:
        reset_correlation_id(token)

    duration_ms = round((time.monotonic() - started) * 1000, 1)
    final_match = result.get("final_match")
    logger.info(
        "Matching completed | %s",
        format_context(
            job_id=job.job_id,
            candidates_evaluated=len(result.get("profile_scores") or []),
            selected_profile=final_match.selected_profile_id if final_match else None,
            score=final_match.match_score if final_match else None,
            recommendation=final_match.recommendation.value if final_match else None,
            duration_ms=duration_ms,
        ),
    )
    return result


def handle_job_discovered(envelope: EventEnvelope[NormalizedJob]) -> None:
    """Sync entry point required by `EventConsumer`'s `EventHandler`
    contract — see this module's docstring for why. Constructible as
    `EventConsumer(Topic.JOBS_DISCOVERED, "job-matching-service",
    handle_job_discovered, dlq_producer=...)`.
    """
    asyncio.run(_handle_job_discovered_async(envelope))


async def _handle_job_discovered_async(envelope: EventEnvelope[NormalizedJob]) -> None:
    job = envelope.payload
    correlation_id = propagate_correlation_id(envelope)

    # Idempotency (Kafka's at-least-once delivery — kafka-topics.md): this
    # handler may see the same jobs.discovered message more than once.
    # Three-way check on job_matches.published_at
    # (database-ownership.md#job_matches's "Publish-reliability column"
    # section — read that section in full before touching this block):
    #
    #   no JobMatch row for this job_id
    #       -> run the full workflow (score, select, persist, publish)
    #   row exists, published_at IS NULL
    #       -> persisted but never successfully published (e.g. the DB
    #          write committed and the Kafka publish then failed). Do NOT
    #          re-score — that would waste LLM calls redoing work already
    #          done and risks a different result for the same decision.
    #          Republish from the already-persisted JobMatch via the same
    #          publish_match_result() helper persist_and_publish uses, then
    #          mark published.
    #   row exists, published_at IS NOT NULL
    #       -> true duplicate redelivery of an already-fully-handled
    #          message; skip, unchanged from before this fix.
    #
    # This is not the profiles.updated re-match path
    # (domain-model.md#jobmatch's "immutable once written ... re-matching
    # after a ProfileUpdatedEvent creates a new JobMatch row" note is about
    # a new profile becoming available, a different, still-deferred code
    # path — see handle_profile_updated below). A plain redelivery of the
    # same jobs.discovered event carries no new matching information.
    async with matching_session_scope() as session:
        existing = await JobMatchRepository(session).get_latest_for_job_with_published_at(
            job.job_id
        )

    if existing is not None:
        job_match, published_at = existing
        if published_at is not None:
            logger.info(
                "job %s already matched and published (job_match_id=%s); "
                "skipping replayed jobs.discovered",
                job.job_id,
                job_match.id,
            )
            return

        logger.info(
            "job %s already matched but not yet published (job_match_id=%s); "
            "retrying publish only, no re-scoring",
            job.job_id,
            job_match.id,
        )
        token = set_correlation_id(correlation_id)
        try:
            await publish_match_result(job_match, job, correlation_id=correlation_id)
        except MatchingError:
            raise
        except Exception as exc:
            raise MatchingError(
                f"unexpected failure republishing job {job.job_id}: {exc}"
            ) from exc
        finally:
            reset_correlation_id(token)
        return

    try:
        await _run_matching(job, correlation_id)
    except MatchingError:
        # Already normalized (carries a duck-typed error_code) — re-raise
        # unchanged so EventConsumer's retry/DLQ mechanism sees it.
        logger.exception("Matching failed | %s", format_context(job_id=job.job_id))
        raise
    except LLMProviderError as exc:
        logger.exception("Matching failed | %s", format_context(job_id=job.job_id, reason="llm_provider_error"))
        raise MatchingError(
            f"unrecoverable LLM provider error matching job {job.job_id}: {exc}",
            error_code=ErrorCode.LLM_PROVIDER_ERROR,
        ) from exc
    except Exception as exc:
        logger.exception("Matching failed | %s", format_context(job_id=job.job_id, reason="unexpected"))
        raise MatchingError(
            f"unexpected failure matching job {job.job_id}: {exc}"
        ) from exc


async def handle_profile_updated(
    envelope: EventEnvelope[ProfileUpdateSummary],
) -> None:
    """Deferred to the Contact Discovery wave.

    component-contracts.md's full Job Matching Service contract documents a
    `profiles.updated` consumer that re-runs JobMatchingState for the
    user's open opportunities not yet SHORTLISTED. This increment's
    explicit scope (per the task that produced this component) is: consume
    only `jobs.discovered`; produce only `jobs.matched` and (conditionally)
    `jobs.shortlisted`. This handler is intentionally left unimplemented —
    not silently dropped or forgotten — and is not wired into any running
    `EventConsumer`. See the implementation report's Issues section.
    """
    raise NotImplementedError(
        "handle_profile_updated is intentionally deferred until re-matching "
        "is in scope — see this function's docstring. Not wired into any "
        "running EventConsumer."
    )


__all__ = [
    "handle_job_discovered",
    "handle_profile_updated",
    "set_graph",
    "set_user_preferences_client",
]
