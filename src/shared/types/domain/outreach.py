"""Outreach — a single piece of generated professional communication, its
approval state, and its send state. This is where the human-in-the-loop
gate lives. See docs/architecture/domain-model.md#outreach.

Ownership: Outreach Service. Modifiable by Outreach Service only. The human
approval action is always mediated through the Outreach Service's API — no
other component, including Tracking, ever writes to this entity.
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.enums import OutreachChannel, OutreachStatus
from shared.types.ids import ContactId, JobId, OutreachId, UserId


class Outreach(BaseModel):
    id: OutreachId
    job_id: JobId
    contact_id: ContactId
    user_id: UserId
    channel: OutreachChannel
    draft_message: str  # generated message
    final_message: str | None = None  # set if the user edits before approving
    status: OutreachStatus
    generated_at: datetime
    decided_at: datetime | None = None  # when approved/rejected
    decided_by: UserId | None = None
    sent_at: datetime | None = None
    external_message_id: str | None = None  # provider-assigned id after send
    send_error: str | None = None  # set only if status == SEND_FAILED
