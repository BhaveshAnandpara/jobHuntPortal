"""ContactScore — the ranking signal breakdown and final relevance score
for one contact, relative to one job. See
docs/architecture/domain-model.md#contactscore.

Ownership: Contact Discovery Service (ranking is a sub-responsibility of
this service). Modifiable by Contact Discovery Service only.
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.ids import ContactId, ContactScoreId, JobId


class ContactScore(BaseModel):
    id: ContactScoreId
    contact_id: ContactId
    job_id: JobId
    relevance_score: float  # 0.0-10.0
    same_company: bool
    department_relevance: float | None = None  # 0.0-1.0
    role_similarity: float | None = None  # 0.0-1.0
    seniority_fit: float | None = None  # 0.0-1.0
    ranked_at: datetime
