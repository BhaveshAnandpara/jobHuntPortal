"""Kafka event payload types not already defined as canonical exchange
types. See docs/architecture/event-contracts.md#payload-types.

Several events reuse an existing canonical type (shared.types.dto) as their
payload rather than defining a second, near-identical type — those are
listed here for discoverability, not redefined:

    JobDiscoveredEvent      -> shared.types.dto.NormalizedJob
    JobMatchedEvent         -> shared.types.dto.JobMatchResult
    JobShortlistedEvent     -> shared.types.dto.JobMatchResult (same type,
                                different topic — see event-contracts.md)
    ContactsFoundEvent      -> shared.types.dto.ContactRankingResult
    OutreachGeneratedEvent  -> shared.types.dto.OutreachDraft
    ApplicationUpdatedEvent -> shared.types.dto.ApplicationStatusUpdate

The four payload types below are event-specific and have no other use
outside the event envelope.
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.enums import OutreachChannel, OutreachDecisionType, ProfileChangeType
from shared.types.ids import JobId, OutreachId, ProfileId, ResumeId, UserId


class ProfileUpdateSummary(BaseModel):
    """Payload of ProfileUpdatedEvent (topic: profiles.updated)."""

    profile_id: ProfileId
    user_id: UserId
    resume_id: ResumeId
    change_type: ProfileChangeType
    updated_at: datetime


class ContactSearchRequest(BaseModel):
    """Payload of ContactsRequestedEvent (topic: contacts.requested)."""

    job_id: JobId
    user_id: UserId
    company: str
    title: str
    location: str | None = None


class OutreachDecision(BaseModel):
    """Payload of OutreachApprovedEvent (topic: outreach.approved)."""

    outreach_id: OutreachId
    job_id: JobId
    user_id: UserId
    decision: OutreachDecisionType
    # set only if decision == APPROVED and user edited the draft
    final_message: str | None = None
    decided_at: datetime
    decided_by: UserId


class OutreachSentConfirmation(BaseModel):
    """Payload of OutreachSentEvent (topic: outreach.sent)."""

    outreach_id: OutreachId
    job_id: JobId
    user_id: UserId
    channel: OutreachChannel
    sent_at: datetime
    external_message_id: str | None = None


__all__ = [
    "ProfileUpdateSummary",
    "ContactSearchRequest",
    "OutreachDecision",
    "OutreachSentConfirmation",
]
