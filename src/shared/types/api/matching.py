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
    matched_at: datetime


__all__ = ["JobMatchResponse"]
