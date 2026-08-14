"""Canonical identifier types.

Every ID in the system is a UUIDv4, wrapped as a distinct type via
`typing.NewType` so that, for example, a `JobId` can never be passed where a
`ContactId` is expected without an explicit cast.

Locked contract — see docs/architecture/shared-types.md#identifiers.
No component may define its own ID type for an entity listed here.
"""

from typing import NewType
from uuid import UUID

UserId = NewType("UserId", UUID)
ResumeId = NewType("ResumeId", UUID)
ProfileId = NewType("ProfileId", UUID)
UserPreferencesId = NewType("UserPreferencesId", UUID)
JobId = NewType("JobId", UUID)
JobSourceId = NewType("JobSourceId", UUID)
JobMatchId = NewType("JobMatchId", UUID)
ContactId = NewType("ContactId", UUID)
ContactScoreId = NewType("ContactScoreId", UUID)
OutreachId = NewType("OutreachId", UUID)
ApplicationId = NewType("ApplicationId", UUID)
ApplicationHistoryId = NewType("ApplicationHistoryId", UUID)
WorkflowExecutionId = NewType("WorkflowExecutionId", UUID)
EventId = NewType("EventId", UUID)
CorrelationId = NewType("CorrelationId", UUID)

__all__ = [
    "UserId",
    "ResumeId",
    "ProfileId",
    "UserPreferencesId",
    "JobId",
    "JobSourceId",
    "JobMatchId",
    "ContactId",
    "ContactScoreId",
    "OutreachId",
    "ApplicationId",
    "ApplicationHistoryId",
    "WorkflowExecutionId",
    "EventId",
    "CorrelationId",
]
