"""Job Matching Service API request/response contracts.
See docs/architecture/api-contracts.md#job-matching-service.

Note: `GET /jobs` returns `list[shared.types.api.jobs.JobResponse]` — there
is no separate response type for that endpoint.

`JobMatchResponse.profile_scores` (Step 10.5 frontend-blocking contract
cleanup) exposes `shared.types.domain.job_match.JobMatch.profile_scores`,
already persisted (`job_matches.profile_scores`) but not previously
projected onto this response type. Additive-only, and reuses the canonical
`ProfileMatchScore` shape from `shared.types.dto` rather than introducing a
second scoring representation.

`reasoning`/`hard_blocker`/`insufficient_detail` follow that same precedent
exactly: they are not new domain fields and not new columns, but a
projection of the *selected* profile's already-persisted
`ProfileMatchScore` (the `profile_scores` entry whose `profile_id ==
selected_profile_id`) onto the top level of this response, so the UI can
show them front-and-center without reading the array. They sit beside
`recommendation`, which is likewise a judgement only this response carries.
`JobMatch`'s own locked field list (docs/architecture/domain-model.md
#jobmatch) is deliberately NOT extended — see `matching.api.routes`.
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.dto import ProfileMatchScore
from shared.types.enums import MatchRecommendation
from shared.types.ids import JobMatchId, ProfileId, ResumeId


class JobMatchResponse(BaseModel):
    job_match_id: JobMatchId
    selected_profile_id: ProfileId
    selected_resume_id: ResumeId
    match_score: float
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    recommendation: MatchRecommendation
    profile_scores: list[ProfileMatchScore] = []
    # Projected from the selected profile's entry in `profile_scores`.
    # Defaults cover both a match persisted before these fields existed and
    # a match whose selected profile has no entry in the array.
    reasoning: str = ""
    hard_blocker: str | None = None
    insufficient_detail: bool = False
    matched_at: datetime


__all__ = ["JobMatchResponse"]
