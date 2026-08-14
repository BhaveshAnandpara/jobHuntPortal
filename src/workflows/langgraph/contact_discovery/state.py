"""ContactDiscoveryState — typed state for the Contact Discovery workflow.
Locked contract — see docs/architecture/langgraph-state.md#contactdiscoverystate.

Owned by Contact Discovery Service. No other component may depend on this
state's shape.
"""

from typing import TypedDict

from shared.types.dto import ContactCandidate, RankedContact, WorkflowError
from shared.types.ids import JobId, UserId


class ContactDiscoveryState(TypedDict):
    job_id: JobId
    user_id: UserId
    company: str
    title: str
    location: str | None

    candidates: list[ContactCandidate]
    ranked_contacts: list[RankedContact]

    errors: list[WorkflowError]
