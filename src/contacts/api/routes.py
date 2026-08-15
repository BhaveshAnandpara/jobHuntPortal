"""Contact Discovery Service route handlers.

Owned endpoints (docs/architecture/api-contracts.md#contact-discovery-service):
    GET  /jobs/{job_id}/contacts
    POST /jobs/{job_id}/contacts/search

--------------------------------------------------------------------------
Architecture gaps (both flagged in this agent's implementation report;
resolved the minimum-surface way the task brief pre-approved for gap 1):

1. api-contracts.md's `POST` validation ("job_id exists, belongs to
   caller, and has Application.status >= SHORTLISTED") cannot be checked
   here: Contact Discovery Service makes zero outbound API calls
   (dependency-graph.md#2-runtime-api-dependencies) and has no read access
   to `jobs`/`applications` (database-ownership.md). Per this component's
   task brief, this validation is skipped; the request is accepted as long
   as `job_id` is a well-formed UUID (FastAPI's path-typed `UUID` already
   enforces that much). The same reasoning extends to `GET
   /jobs/{job_id}/contacts`'s documented 404 `NOT_FOUND` — an unknown or
   not-yet-searched `job_id` is indistinguishable, from this service's own
   data, from "no contacts found yet", so it returns an empty 200 list
   rather than 404.

2. `POST /jobs/{job_id}/contacts/search`'s documented input is only the
   path param `job_id`, but the event it must publish
   (`ContactSearchRequest`) requires `user_id`/`company`/`title` — data
   this component cannot look up itself (same zero-outbound-call
   constraint as gap 1; `company`/`title` live on `jobs`, owned by Job
   Ingestion/Job Discovery Service). Resolved by accepting them as an
   additive request body (`TriggerContactSearchRequest`,
   shared/types/api/contacts.py) rather than leaving the endpoint unable
   to fulfill its own documented contract.
--------------------------------------------------------------------------
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, status

from contacts.api.dependencies import EventProducerDep, SessionDep
from contacts.repository import list_contacts_with_relevance
from infrastructure.auth import CurrentUserIdDependency
from infrastructure.kafka.topics import Topic
from shared.events.payloads import ContactSearchRequest
from shared.types.api.contacts import (
    ContactResponse,
    TriggerContactSearchRequest,
    TriggerContactSearchResponse,
)
from shared.types.ids import JobId

router = APIRouter(tags=["contacts"])


@router.get("/jobs/{job_id}/contacts", response_model=list[ContactResponse])
async def get_job_contacts(job_id: UUID, session: SessionDep) -> list[ContactResponse]:
    """See gap 1 in this module's docstring — no job-existence check is
    possible from this component."""
    rows = await list_contacts_with_relevance(session, JobId(job_id))
    return [
        ContactResponse(
            id=contact.id,
            full_name=contact.full_name,
            headline=contact.headline,
            company=contact.company,
            contact_type=contact.contact_type,
            profile_url=contact.profile_url,
            relevance_score=relevance_score if relevance_score is not None else 0.0,
            status=contact.status,
        )
        for contact, relevance_score in rows
    ]


@router.post(
    "/jobs/{job_id}/contacts/search",
    response_model=TriggerContactSearchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_contact_search(
    job_id: UUID,
    request: TriggerContactSearchRequest,
    user_id: CurrentUserIdDependency,
    producer: EventProducerDep,
) -> TriggerContactSearchResponse:
    """Thin producer only — publishes `ContactsRequestedEvent` onto the
    same `contacts.requested` command topic Job Matching Service uses
    automatically (kafka-topics.md's design-decision note grants this
    component producer rights here for exactly this manual re-trigger,
    "not a self-call"). The result arrives later via `contacts.found`,
    handled by this component's own `contacts.requested` consumer just
    like the automatic path — see gap 2 in this module's docstring for why
    the request body carries `company`/`title` (`user_id` is now
    token-derived rather than a third additive body field).
    """
    requested_at = datetime.now(UTC)
    payload = ContactSearchRequest(
        job_id=JobId(job_id),
        user_id=user_id,
        company=request.company,
        title=request.title,
        location=request.location,
    )
    producer.publish(Topic.CONTACTS_REQUESTED, payload)
    return TriggerContactSearchResponse(job_id=JobId(job_id), requested_at=requested_at)


__all__ = ["router"]
