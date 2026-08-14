"""Tracking Service API request/response contracts.
See docs/architecture/api-contracts.md#tracking-service.
"""

from datetime import date, datetime

from pydantic import BaseModel

from shared.types.enums import ApplicationStatus, EventType
from shared.types.ids import (
    ApplicationHistoryId,
    ApplicationId,
    ContactId,
    CorrelationId,
    JobId,
    ResumeId,
    UserId,
)


class ApplicationResponse(BaseModel):
    id: ApplicationId
    job_id: JobId
    user_id: UserId
    company: str
    title: str
    status: ApplicationStatus
    selected_resume_id: ResumeId | None = None
    match_score: float | None = None
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    referral_contact_id: ContactId | None = None
    applied_date: date | None = None
    follow_up_date: date | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class UpdateApplicationStatusRequest(BaseModel):
    new_status: ApplicationStatus
    applied_date: date | None = None
    notes: str | None = None


class ApplicationHistoryResponse(BaseModel):
    id: ApplicationHistoryId
    application_id: ApplicationId
    from_status: ApplicationStatus | None = None
    to_status: ApplicationStatus
    changed_at: datetime
    triggered_by: str
    source_event_type: EventType | None = None
    correlation_id: CorrelationId | None = None


__all__ = [
    "ApplicationResponse",
    "UpdateApplicationStatusRequest",
    "ApplicationHistoryResponse",
]
