"""Job Matching Service route handlers.

Owned endpoints (docs/architecture/api-contracts.md#job-matching-service):
    GET /jobs                    -- NOT implemented, see module docstring below
    GET /jobs/{job_id}           -- NOT implemented, see module docstring below
    GET /jobs/{job_id}/matches   -- implemented

--------------------------------------------------------------------------
Architecture gap: GET /jobs and GET /jobs/{job_id} are documented in
api-contracts.md#job-matching-service, but this component does not own the
`jobs` table (database-ownership.md#jobs — owned by Job Ingestion Service /
Job Discovery Service) and no Job-owning component exposes a read API for
arbitrary Job lookups today: Job Ingestion Service only exposes
`POST /jobs/ingest-url`, Job Discovery Service only exposes
`GET/POST /job-sources` (component-contracts.md). `ownership.md`'s "prefer
a contract over reaching into internal state" rule forbids a direct
cross-component table read or importing `jobs.models`/`jobs.repository`
from this component's application code.

This is the same category of gap as the previously-resolved `GET
/users/{id}` gap from the Wave 1 cleanup round: a real, unresolved
architecture question (does Job Ingestion/Job Discovery Service need to
expose a `GET /jobs`-shaped read API of its own, which Job Matching Service
would then have to call and re-expose or proxy? or should these two
endpoints move to whichever component ends up owning general Job reads?),
not something to resolve unilaterally here. Reported in the implementation
report's Issues section rather than worked around.
--------------------------------------------------------------------------
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from matching.api.dependencies import get_job_match_repository
from matching.repository import JobMatchRepository
from shared.errors.codes import ErrorCode
from shared.types.api.matching import JobMatchResponse
from shared.types.domain.job_match import JobMatch
from shared.types.ids import JobId

router = APIRouter(tags=["matching"])

JobMatchRepositoryDep = Annotated[JobMatchRepository, Depends(get_job_match_repository)]


def _job_match_response(job_match: JobMatch) -> JobMatchResponse:
    """Project a persisted `JobMatch` onto its API response.

    `reasoning`/`hard_blocker`/`insufficient_detail` are read off the
    *selected* profile's `ProfileMatchScore` — the same entry whose score
    already became `JobMatch.match_score` (see
    `workflows.langgraph.job_matching.nodes.persist_and_publish`, which
    picks the winner the same way). Derived here at response-construction
    time rather than stored a second time: they are already persisted
    inside `job_matches.profile_scores`, so this needs no new column and no
    migration, and it cannot drift from the array it is read from.

    `JobMatch` itself is deliberately not given these three as top-level
    fields: its shape is locked in docs/architecture/domain-model.md
    #jobmatch. `ProfileMatchScore`, which does carry them, is not locked in
    that table — it is listed there only as the element type of the
    optional `profile_scores` field.

    A match persisted before these fields existed, or one whose selected
    profile is somehow absent from `profile_scores`, falls through to the
    response model's defaults ("", None, False) — every one of which the UI
    treats as "not present".
    """
    selected_score = next(
        (
            score
            for score in job_match.profile_scores
            if score.profile_id == job_match.selected_profile_id
        ),
        None,
    )

    return JobMatchResponse(
        job_match_id=job_match.id,
        selected_profile_id=job_match.selected_profile_id,
        selected_resume_id=job_match.selected_resume_id,
        match_score=job_match.match_score,
        matched_skills=job_match.matched_skills,
        missing_skills=job_match.missing_skills,
        recommendation=job_match.recommendation,
        profile_scores=job_match.profile_scores,
        reasoning=selected_score.reasoning if selected_score else "",
        hard_blocker=selected_score.hard_blocker if selected_score else None,
        insufficient_detail=(
            selected_score.insufficient_detail if selected_score else False
        ),
        matched_at=job_match.matched_at,
    )


@router.get("/jobs/{job_id}/matches", response_model=JobMatchResponse)
async def get_job_matches(
    job_id: UUID,
    repository: JobMatchRepositoryDep,
) -> JobMatchResponse:
    job_match = await repository.get_latest_for_job(JobId(job_id))
    if job_match is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": ErrorCode.NOT_FOUND.value,
                "message": f"no JobMatch found for job {job_id}",
            },
        )
    return _job_match_response(job_match)


__all__ = ["router"]
