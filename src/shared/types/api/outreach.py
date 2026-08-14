"""Outreach Service API request/response contracts.
See docs/architecture/api-contracts.md#outreach-service.
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.enums import OutreachChannel, OutreachStatus
from shared.types.ids import ContactId, JobId, OutreachId


class OutreachResponse(BaseModel):
    id: OutreachId
    job_id: JobId
    contact_id: ContactId
    channel: OutreachChannel
    draft_message: str
    final_message: str | None = None
    status: OutreachStatus
    generated_at: datetime
    decided_at: datetime | None = None
    sent_at: datetime | None = None


class ApproveOutreachRequest(BaseModel):
    final_message: str | None = None


class EditOutreachRequest(BaseModel):
    message: str


__all__ = ["OutreachResponse", "ApproveOutreachRequest", "EditOutreachRequest"]
