"""OutreachGenerationState — typed state for the Outreach Generation
workflow. Locked contract — see
docs/architecture/langgraph-state.md#outreachgenerationstate.

Owned by Outreach Service. No other component may depend on this state's
shape.
"""

from typing import TypedDict

from shared.types.dto import RankedContact, ResumeProfile, WorkflowError
from shared.types.enums import OutreachChannel
from shared.types.ids import JobId, ResumeId, UserId


class OutreachGenerationState(TypedDict):
    job_id: JobId
    user_id: UserId
    contact: RankedContact
    candidate_profile: ResumeProfile
    selected_resume_id: ResumeId

    channel: OutreachChannel | None
    draft_message: str | None

    errors: list[WorkflowError]
