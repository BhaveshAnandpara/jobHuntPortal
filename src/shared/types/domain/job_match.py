"""JobMatch — the analytical record of comparing one Job against a user's
CandidateProfiles and selecting the best one. This is the Job Matching
Service's own record of *how* it reached a decision — distinct from
Application, which is the user-facing lifecycle record (owned by
Tracking). See docs/architecture/domain-model.md#jobmatch.

Ownership: Job Matching Service. Modifiable by Job Matching Service only.
Immutable once written in practice (re-matching creates a new row rather
than mutating the old one, preserving history).
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.dto import ProfileMatchScore
from shared.types.enums import MatchRecommendation
from shared.types.ids import JobId, JobMatchId, ProfileId, ResumeId, UserId


class JobMatch(BaseModel):
    id: JobMatchId
    job_id: JobId
    user_id: UserId
    selected_profile_id: ProfileId
    selected_resume_id: ResumeId
    match_score: float  # 0.0-1.0
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    recommendation: MatchRecommendation
    # score against every evaluated profile, not just the winner
    profile_scores: list[ProfileMatchScore] = []
    matched_at: datetime
