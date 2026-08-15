"""Contact Discovery Service API request/response contracts.
See docs/architecture/api-contracts.md#contact-discovery-service.

`TriggerContactSearchRequest` is an addition beyond api-contracts.md's
documented input for `POST /jobs/{job_id}/contacts/search` (that document
lists only the path param `job_id`) — flagged as an architecture gap in
this component's implementation report rather than applied silently.
`ContactSearchRequest` (event-contracts.md, the payload this endpoint must
publish) requires `user_id`/`company`/`title`, and this component has no
way to obtain them itself: dependency-graph.md#2-runtime-api-dependencies
grants Contact Discovery Service zero outbound API calls, and it has no
read access to the `jobs`/`applications` tables that hold this data
(database-ownership.md). Additive-only per shared-types.md's versioning
rules (a new request type, not a changed field on an existing one) — see
contacts/api/routes.py's module docstring for the full reasoning.
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.enums import ContactStatus, ContactType
from shared.types.ids import ContactId, JobId


class ContactResponse(BaseModel):
    id: ContactId
    full_name: str
    headline: str | None = None
    company: str
    contact_type: ContactType
    profile_url: str | None = None
    relevance_score: float
    status: ContactStatus


class TriggerContactSearchRequest(BaseModel):
    """See this module's docstring — an additive gap-resolution type, not
    part of api-contracts.md's documented (path-param-only) input."""

    company: str
    title: str
    location: str | None = None


class TriggerContactSearchResponse(BaseModel):
    job_id: JobId
    requested_at: datetime


__all__ = [
    "ContactResponse",
    "TriggerContactSearchRequest",
    "TriggerContactSearchResponse",
]
