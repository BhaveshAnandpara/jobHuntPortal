"""Contact — a person discovered at the target company who may be useful
for referral or networking outreach. See
docs/architecture/domain-model.md#contact.

Ownership: Contact Discovery Service. Modifiable by Contact Discovery
Service only.
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.enums import ContactStatus, ContactType
from shared.types.ids import ContactId, JobId, UserId


class Contact(BaseModel):
    id: ContactId
    job_id: JobId  # the opportunity this contact was found for
    user_id: UserId
    full_name: str
    headline: str | None = None  # e.g. "Senior Mechanical Engineer at Acme"
    company: str
    contact_type: ContactType  # generic across professions
    profile_url: str | None = None  # e.g. LinkedIn profile URL
    email: str | None = None  # if discoverable
    status: ContactStatus
    discovered_at: datetime
