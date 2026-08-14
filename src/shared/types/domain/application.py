"""Application — the single, canonical, user-facing lifecycle record for
one opportunity — from discovery through offer/rejection. This is "the
tracker" referenced throughout about_project.md. Its `status` field *is*
the opportunity lifecycle state machine. See
docs/architecture/domain-model.md#application.

Application is intentionally the aggregation point: it is built entirely
by consuming events produced by every other component, never by those
components writing to it directly.

Ownership: Tracking Service. Modifiable by Tracking Service only —
including for status changes that originate from a user action, which go
through the Tracking Service's own API, not through the originating
component.
"""

from datetime import date, datetime

from pydantic import BaseModel

from shared.types.enums import ApplicationStatus
from shared.types.ids import ApplicationId, ContactId, JobId, ResumeId, UserId


class Application(BaseModel):
    id: ApplicationId
    job_id: JobId  # 1:1 with Job
    user_id: UserId
    company: str  # denormalized from Job for fast listing
    title: str  # denormalized from Job
    status: ApplicationStatus
    selected_resume_id: ResumeId | None = None  # populated once matched
    match_score: float | None = None  # populated once matched
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    # populated if an outreach path was used
    referral_contact_id: ContactId | None = None
    applied_date: date | None = None  # set when status reaches APPLIED
    follow_up_date: date | None = None  # user- or system-suggested
    notes: str | None = None  # free-text user notes
    created_at: datetime
    updated_at: datetime
