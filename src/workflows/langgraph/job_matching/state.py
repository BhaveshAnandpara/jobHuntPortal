"""JobMatchingState — typed state for the Job Matching workflow.
Locked contract — see docs/architecture/langgraph-state.md#jobmatchingstate.

Owned by Job Matching Service. No other component may depend on this
state's shape (docs/architecture/ownership.md#rule-prefer-a-contract-over-reaching-into-internal-state).
"""

from typing import TypedDict

from shared.types.dto import JobMatchResult, NormalizedJob, ProfileMatchScore, ResumeProfile, WorkflowError
from shared.types.domain.user_preferences import UserPreferences
from shared.types.enums import MatchRecommendation
from shared.types.ids import ResumeId


class JobMatchingState(TypedDict):
    # input, set once at graph entry, never mutated by nodes
    job: NormalizedJob
    profiles: list[ResumeProfile]
    preferences: UserPreferences

    # working state, populated by nodes
    profile_scores: list[ProfileMatchScore]
    selected_profile: ResumeProfile | None
    selected_resume_id: ResumeId | None
    recommendation: MatchRecommendation | None

    # output, set by the final node
    final_match: JobMatchResult | None

    errors: list[WorkflowError]
