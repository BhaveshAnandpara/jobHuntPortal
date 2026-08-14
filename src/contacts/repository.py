"""Persistence boundary for Contact Discovery Service's owned tables
(`contacts`, `contact_rankings`). Implements the shared.contracts.Repository
interface.

Repositories take and return the canonical domain types
(`shared.types.domain.contact.Contact`,
`shared.types.domain.contact_score.ContactScore`); the `ContactRecord`/
`ContactScoreRecord` SQLAlchemy models in models.py never leave this
module — mirrors `matching/repository.py`'s `_to_domain`-style projection
pattern.

No other component may import this module.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contacts.models import ContactRecord, ContactScoreRecord
from shared.types.domain.contact import Contact
from shared.types.domain.contact_score import ContactScore
from shared.types.ids import ContactId, ContactScoreId, JobId, UserId


def _contact_to_domain(record: ContactRecord) -> Contact:
    return Contact(
        id=ContactId(record.id),
        job_id=JobId(record.job_id),
        user_id=UserId(record.user_id),
        full_name=record.full_name,
        headline=record.headline,
        company=record.company,
        contact_type=record.contact_type,
        profile_url=record.profile_url,
        email=record.email,
        status=record.status,
        discovered_at=record.discovered_at,
    )


def _score_to_domain(record: ContactScoreRecord) -> ContactScore:
    return ContactScore(
        id=ContactScoreId(record.id),
        contact_id=ContactId(record.contact_id),
        job_id=JobId(record.job_id),
        relevance_score=record.relevance_score,
        same_company=record.same_company,
        department_relevance=record.department_relevance,
        role_similarity=record.role_similarity,
        seniority_fit=record.seniority_fit,
        ranked_at=record.ranked_at,
    )


class ContactRepository:
    """Reads/writes only `contacts`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, contact_id: ContactId) -> Contact | None:
        record = await self._session.get(ContactRecord, UUID(str(contact_id)))
        return _contact_to_domain(record) if record is not None else None

    async def list_for_job(self, job_id: JobId) -> list[Contact]:
        result = await self._session.scalars(
            select(ContactRecord).where(ContactRecord.job_id == UUID(str(job_id)))
        )
        return [_contact_to_domain(record) for record in result.all()]

    async def add(self, contact: Contact) -> Contact:
        self._session.add(
            ContactRecord(
                id=contact.id,
                job_id=contact.job_id,
                user_id=contact.user_id,
                full_name=contact.full_name,
                headline=contact.headline,
                company=contact.company,
                contact_type=contact.contact_type,
                profile_url=contact.profile_url,
                email=contact.email,
                status=contact.status,
                discovered_at=contact.discovered_at,
            )
        )
        await self._session.flush()
        return contact


class ContactScoreRepository:
    """Reads/writes only `contact_rankings`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_contact(self, contact_id: ContactId) -> ContactScore | None:
        result = await self._session.scalars(
            select(ContactScoreRecord).where(
                ContactScoreRecord.contact_id == UUID(str(contact_id))
            )
        )
        record = result.first()
        return _score_to_domain(record) if record is not None else None

    async def add(self, score: ContactScore) -> ContactScore:
        self._session.add(
            ContactScoreRecord(
                id=score.id,
                contact_id=score.contact_id,
                job_id=score.job_id,
                relevance_score=score.relevance_score,
                same_company=score.same_company,
                department_relevance=score.department_relevance,
                role_similarity=score.role_similarity,
                seniority_fit=score.seniority_fit,
                ranked_at=score.ranked_at,
            )
        )
        await self._session.flush()
        return score


async def list_contacts_with_relevance(
    session: AsyncSession, job_id: JobId
) -> list[tuple[Contact, float | None]]:
    """Combined read for `GET /jobs/{job_id}/contacts`
    (api-contracts.md#contact-discovery-service — response includes
    `relevance_score`, which lives on `contact_rankings`, not `contacts`).

    Deliberately not a method on `ContactRepository`/`ContactScoreRepository`
    — those two stay 1:1 with the table each owns (matching
    `JobMatchRepository`'s precedent of one repository per owned table);
    this is a small combined-read helper local to this component's own API
    layer, doing one LEFT JOIN rather than an N+1 query per contact.
    """
    result = await session.execute(
        select(ContactRecord, ContactScoreRecord.relevance_score)
        .join(ContactScoreRecord, ContactScoreRecord.contact_id == ContactRecord.id, isouter=True)
        .where(ContactRecord.job_id == UUID(str(job_id)))
    )
    return [(_contact_to_domain(row[0]), row[1]) for row in result.all()]


__all__ = [
    "ContactRepository",
    "ContactScoreRepository",
    "list_contacts_with_relevance",
]
