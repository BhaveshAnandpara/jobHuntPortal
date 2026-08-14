"""Node functions for the Job Matching LangGraph workflow.
See docs/architecture/langgraph-state.md#jobmatchingstate for the graph
shape and per-node input/output/error contract:

    load_profiles -> score_profile (fan-out, one per profile)
                   -> select_best_profile -> compute_recommendation
                   -> persist_and_publish

Owned by Job Matching Service.

Dependency injection: each node that uses an "external tool" (documented
per-node in langgraph-state.md) reaches a lazily-constructed, module-level
default (a real HTTP client, a real `LLMClient`, the shared DB session
scope from `matching.db`, and `matching.events`' producer) — the same
lazy-singleton idiom every other component's `api/dependencies.py` already
uses. Tests override via the `set_*` functions exported below rather than
patching internals directly.

`score_profile` is documented as "invoked once per profile in
state['profiles'], fanned out." `JobMatchingState` (state.py) is a locked
TypedDict with a plain `profile_scores: list[ProfileMatchScore]` field —
no `Annotated[..., reducer]`. LangGraph requires a reducer-annotated field
to safely merge concurrent partial-state updates from a `Send`-based fan-out
to the same key (verified empirically: un-annotated concurrent writes raise
`InvalidUpdateError: Can receive only one value per step`). Since state.py
is a locked contract this component may not modify, `score_profile` is
implemented as a single node that iterates `state['profiles']` sequentially
in-process (still scoring and error-handling each profile independently,
per the node's documented per-profile error contract) rather than using
LangGraph's `Send` mechanism. This is a deliberate, reported deviation from
the "e.g. Send" phrasing in the task brief — see the implementation
report's Issues section.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from infrastructure.llm import LLMClient, LLMProviderError
from matching.clients import ProfileServiceClient
from matching.db import matching_session_scope
from matching.errors import MatchingError
from matching.events import (
    publish_contacts_requested,
    publish_job_matched,
    publish_job_shortlisted,
)
from matching.repository import JobMatchRepository, JobProcessingStatusRepository
from shared.errors.codes import ErrorCode
from shared.events.payloads import ContactSearchRequest
from shared.types.domain.job_match import JobMatch
from shared.types.dto import (
    JobMatchResult,
    NormalizedJob,
    ProfileMatchScore,
    WorkflowError,
)
from shared.types.enums import JobProcessingStatus, MatchRecommendation, ProfileStatus
from shared.types.ids import CorrelationId, JobMatchId
from workflows.langgraph.job_matching.context import get_correlation_id
from workflows.langgraph.job_matching.scoring import score_profile_with_llm
from workflows.langgraph.job_matching.state import JobMatchingState

# ---------------------------------------------------------------------------
# Recommendation thresholds (compute_recommendation)
# ---------------------------------------------------------------------------

SHORTLIST_THRESHOLD = 0.75
"""score >= this -> MatchRecommendation.SHORTLIST."""

BORDERLINE_THRESHOLD = 0.45
"""BORDERLINE_THRESHOLD <= score < SHORTLIST_THRESHOLD -> BORDERLINE;
score < BORDERLINE_THRESHOLD -> IGNORE. Chosen so a total per-job LLM
failure (every profile recorded at score=0.0, per score_profile's
documented fallback) naturally lands in IGNORE via this ordinary threshold
logic, without a separate graph edge — see score_profile's docstring.
"""

# ---------------------------------------------------------------------------
# Dependency injection seams
# ---------------------------------------------------------------------------

_profile_service_client: ProfileServiceClient | None = None
_llm_client: LLMClient | None = None


def _get_profile_service_client() -> ProfileServiceClient:
    global _profile_service_client
    if _profile_service_client is None:
        _profile_service_client = ProfileServiceClient()
    return _profile_service_client


def set_profile_service_client(client: ProfileServiceClient | None) -> None:
    """Test seam. Pass `None` to restore the lazily-constructed default."""
    global _profile_service_client
    _profile_service_client = client


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
# Shared publish-and-mark-published helper
# ---------------------------------------------------------------------------


async def publish_match_result(
    job_match: JobMatch, job: NormalizedJob, *, correlation_id: CorrelationId | None
) -> JobMatchResult:
    """Publish `jobs.matched` (+ `jobs.shortlisted` and `contacts.requested`
    if `recommendation == SHORTLIST`) for an *already-persisted* `JobMatch`,
    then mark it published (database-ownership.md#job_matches's
    "Publish-reliability column" section).

    `job` supplies `company`/`title`/`location` for `ContactSearchRequest`
    (kafka-topics.md#contactsrequested) — `JobMatch`/`JobMatchResult` don't
    carry those fields, only the originating `NormalizedJob` does. Both
    callers already have it in scope (`persist_and_publish`'s `state["job"]`;
    `matching.consumers`' redelivered `envelope.payload`), so this is not an
    extra fetch.

    Shared by two callers so the publish+mark-published behavior is
    implemented exactly once:
      - `persist_and_publish`, right after inserting a brand-new
        `JobMatch` row (published_at starts `NULL`).
      - `matching.consumers`' idempotency check, when a `jobs.discovered`
        redelivery finds a `JobMatch` row that was persisted but never
        successfully published (published_at still `NULL`) — that path
        skips re-scoring entirely and calls this directly on the
        already-persisted row.

    Raises `MatchingError` on any publish or DB failure — never a raw
    exception — so the Kafka consumer's retry/DLQ mechanism can act on it.
    `published_at` is only ever set forward (see models.py); this function
    never clears it.
    """
    result = JobMatchResult(
        job_match_id=job_match.id,
        job_id=job_match.job_id,
        user_id=job_match.user_id,
        selected_profile_id=job_match.selected_profile_id,
        selected_resume_id=job_match.selected_resume_id,
        match_score=job_match.match_score,
        matched_skills=job_match.matched_skills,
        missing_skills=job_match.missing_skills,
        recommendation=job_match.recommendation,
        matched_at=job_match.matched_at,
    )

    try:
        await publish_job_matched(result, correlation_id=correlation_id)
        if job_match.recommendation == MatchRecommendation.SHORTLIST:
            await publish_job_shortlisted(result, correlation_id=correlation_id)
            await publish_contacts_requested(
                ContactSearchRequest(
                    job_id=job.job_id,
                    user_id=job.user_id,
                    company=job.company,
                    title=job.title,
                    location=job.location,
                ),
                correlation_id=correlation_id,
            )
        async with matching_session_scope() as session:
            await JobMatchRepository(session).mark_published(
                job_match.id, datetime.now(UTC)
            )
    except Exception as exc:
        raise MatchingError(
            f"failed to publish match result for job {job_match.job_id}: {exc}"
        ) from exc

    return result


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def load_profiles(state: JobMatchingState) -> JobMatchingState:
    """External tools: Resume/Profile Service API (GET /profiles).
    Short-circuits to persist_and_publish with recommendation=IGNORE and a
    NO_PROFILES_AVAILABLE error entry if the user has no active profiles.

    `GET /profiles` returns every profile for the user regardless of
    status (api-contracts.md#resumeprofile-service does not document a
    status filter) — this node keeps only `ProfileStatus.ACTIVE` ones, per
    component-contracts.md#job-matching-service's validation rule ("user_id
    has at least one CandidateProfile with status=ACTIVE").
    """
    job = state["job"]
    client = _get_profile_service_client()
    all_profiles = await client.list_profiles(job.user_id)
    active_profiles = [p for p in all_profiles if p.status == ProfileStatus.ACTIVE]

    if not active_profiles:
        return {
            **state,
            "profiles": [],
            "recommendation": MatchRecommendation.IGNORE,
            "errors": [
                *state["errors"],
                WorkflowError(
                    node="load_profiles",
                    error_code=ErrorCode.NO_PROFILES_AVAILABLE,
                    message=f"user {job.user_id} has no ACTIVE candidate profiles",
                    occurred_at=datetime.now(UTC),
                ),
            ],
        }

    return {**state, "profiles": active_profiles}


async def score_profile(state: JobMatchingState) -> JobMatchingState:
    """Scores every profile in state["profiles"] against state["job"].
    External tools: LLM Provider Layer (semantic scoring), via
    `workflows.langgraph.job_matching.scoring.score_profile_with_llm`. A
    single profile's LLM_PROVIDER_ERROR is recorded with score=0.0 and an
    errors entry; scoring continues for the remaining profiles rather than
    aborting. See this module's docstring for why every profile is scored
    within one node invocation instead of a concurrent `Send` fan-out.
    """
    llm_client = _get_llm_client()
    job = state["job"]
    preferences = state["preferences"]

    scores: list[ProfileMatchScore] = list(state["profile_scores"])
    errors: list[WorkflowError] = list(state["errors"])

    for profile in state["profiles"]:
        try:
            score = score_profile_with_llm(llm_client, job, profile, preferences)
        except LLMProviderError as exc:
            score = ProfileMatchScore(
                profile_id=profile.profile_id,
                resume_id=profile.resume_id,
                score=0.0,
                matched_skills=[],
                missing_skills=[],
            )
            errors.append(
                WorkflowError(
                    node="score_profile",
                    error_code=ErrorCode.LLM_PROVIDER_ERROR,
                    message=(
                        f"scoring failed for profile {profile.profile_id} "
                        f"against job {job.job_id}: {exc}"
                    ),
                    occurred_at=datetime.now(UTC),
                )
            )
        scores.append(score)

    return {**state, "profile_scores": scores, "errors": errors}


async def select_best_profile(state: JobMatchingState) -> JobMatchingState:
    """Pure selection over state["profile_scores"] — no external tools.
    Ties are broken by first occurrence in state["profiles"] order
    (`max()` returns the first element with the maximum key value it
    encounters), which keeps selection deterministic.
    """
    scores = state["profile_scores"]
    if not scores:
        # Defensive only — the graph routes straight to persist_and_publish
        # when state["profiles"] is empty, so this node is never entered
        # with an empty profile_scores list in the compiled graph.
        return {**state, "selected_profile": None, "selected_resume_id": None}

    best = max(scores, key=lambda s: s.score)
    selected_profile = next(
        (p for p in state["profiles"] if p.profile_id == best.profile_id), None
    )
    return {
        **state,
        "selected_profile": selected_profile,
        "selected_resume_id": best.resume_id,
    }


async def compute_recommendation(state: JobMatchingState) -> JobMatchingState:
    """Threshold logic over the selected profile's score — no external
    tools. See SHORTLIST_THRESHOLD/BORDERLINE_THRESHOLD above.

    `select_best_profile` already picked the argmax-scoring profile, so the
    best score in `profile_scores` *is* the selected profile's score —
    no need to look it up by id again.
    """
    best_score = max((s.score for s in state["profile_scores"]), default=0.0)

    if best_score >= SHORTLIST_THRESHOLD:
        recommendation = MatchRecommendation.SHORTLIST
    elif best_score >= BORDERLINE_THRESHOLD:
        recommendation = MatchRecommendation.BORDERLINE
    else:
        recommendation = MatchRecommendation.IGNORE

    return {**state, "recommendation": recommendation}


async def persist_and_publish(state: JobMatchingState) -> JobMatchingState:
    """External tools: job_matches repository (DB write), Kafka producer.
    Publishes JobMatchedEvent always, plus JobShortlistedEvent and
    ContactsRequestedEvent if recommendation == SHORTLIST. On
    MATCHING_FAILED, raises `MatchingError` so the Kafka consumer's
    retry/DLQ mechanism can act on it (no partial event is published — see
    this function's inline notes).

    NO_PROFILES_AVAILABLE short-circuit: `JobMatch`/`JobMatchResult` both
    require `selected_profile_id`/`selected_resume_id` as non-optional
    fields (domain-model.md#jobmatch, shared-types.md#jobmatchresult) —
    with zero ACTIVE profiles there is no profile to reference, so no
    well-formed `JobMatch` row or `JobMatchedEvent` can be produced.
    component-contracts.md's jobs.discovered consumer entry says "skip
    matching" for this case, which is taken literally here: no DB insert,
    no Kafka publish. `Job.processing_status` still needs a terminal value
    (state-machines.md: NORMALIZED can only become MATCHED or FAILED) —
    FAILED is used, since no match was produced. Flagged as a real
    architecture gap in the implementation report (langgraph-state.md's
    literal wording — "routes to persist_and_publish with
    recommendation=IGNORE" — doesn't itself resolve the required-field
    conflict).
    """
    job = state["job"]
    errors = list(state["errors"])

    if not state["profiles"]:
        try:
            async with matching_session_scope() as session:
                await JobProcessingStatusRepository(session).mark_status(
                    job.job_id, JobProcessingStatus.FAILED
                )
        except Exception as exc:
            raise MatchingError(
                f"failed to record FAILED status for job {job.job_id} "
                f"(NO_PROFILES_AVAILABLE): {exc}"
            ) from exc
        return {**state, "final_match": None, "errors": errors}

    selected_profile = state["selected_profile"]
    selected_resume_id = state["selected_resume_id"]
    recommendation = state["recommendation"]
    if selected_profile is None or selected_resume_id is None or recommendation is None:
        raise MatchingError(
            f"persist_and_publish reached for job {job.job_id} without a "
            "selected profile/resume/recommendation — the graph must run "
            "select_best_profile and compute_recommendation first"
        )

    winning_score = next(
        (
            s
            for s in state["profile_scores"]
            if s.profile_id == selected_profile.profile_id
        ),
        None,
    )
    matched_skills = winning_score.matched_skills if winning_score else []
    missing_skills = winning_score.missing_skills if winning_score else []
    match_score = winning_score.score if winning_score else 0.0

    job_match = JobMatch(
        id=JobMatchId(uuid4()),
        job_id=job.job_id,
        user_id=job.user_id,
        selected_profile_id=selected_profile.profile_id,
        selected_resume_id=selected_resume_id,
        match_score=match_score,
        matched_skills=matched_skills,
        missing_skills=missing_skills,
        recommendation=recommendation,
        profile_scores=state["profile_scores"],
        matched_at=datetime.now(UTC),
    )
    # published_at starts NULL (models.py's default) — publish_match_result
    # below is what sets it, once jobs.matched (+ jobs.shortlisted) have
    # actually gone out. If persistence succeeds here but publish_match_result
    # then fails, the row is left exactly in the "persisted, not yet
    # published" state matching.consumers' idempotency check knows to
    # resume from — see database-ownership.md#job_matches's
    # "Publish-reliability column" section.

    try:
        async with matching_session_scope() as session:
            await JobMatchRepository(session).add(job_match)
            await JobProcessingStatusRepository(session).mark_status(
                job.job_id, JobProcessingStatus.MATCHED
            )
    except Exception as exc:
        raise MatchingError(
            f"failed to persist JobMatch for job {job.job_id}: {exc}"
        ) from exc

    result = await publish_match_result(
        job_match, job, correlation_id=get_correlation_id()
    )

    return {**state, "final_match": result, "errors": errors}


__all__ = [
    "BORDERLINE_THRESHOLD",
    "SHORTLIST_THRESHOLD",
    "compute_recommendation",
    "load_profiles",
    "persist_and_publish",
    "publish_match_result",
    "score_profile",
    "select_best_profile",
    "set_llm_client",
    "set_profile_service_client",
]
