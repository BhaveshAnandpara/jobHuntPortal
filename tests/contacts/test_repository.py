"""Tests for `contacts.repository.ContactRepository`/`ContactScoreRepository`
against a real (SQLite in-memory) async SQLAlchemy session —
docs/architecture/database-ownership.md#contacts and #contact_rankings.

Scenario H (persistence): Contact/ContactScore persisted with correct
ownership fields, readable back via the repository, Contact.status RANKED.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from contacts.repository import (
    ContactRepository,
    ContactScoreRepository,
    list_contacts_with_relevance,
)
from shared.types.domain.contact import Contact
from shared.types.domain.contact_score import ContactScore
from shared.types.enums import ContactStatus, ContactType
from shared.types.ids import ContactId, ContactScoreId, JobId, UserId

pytestmark = pytest.mark.asyncio


def _contact(**overrides: object) -> Contact:
    fields: dict[str, object] = {
        "id": ContactId(uuid4()),
        "job_id": JobId(uuid4()),
        "user_id": UserId(uuid4()),
        "full_name": "Jordan Smith",
        "headline": "Mechanical Design Engineer at Acme Robotics",
        "company": "Acme Robotics",
        "contact_type": ContactType.PRACTITIONER,
        "profile_url": "https://example.com/in/jordan-smith",
        "email": "jordan@example.com",
        "status": ContactStatus.RANKED,
        "discovered_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return Contact(**fields)


def _score(contact_id: ContactId, job_id: JobId, **overrides: object) -> ContactScore:
    fields: dict[str, object] = {
        "id": ContactScoreId(uuid4()),
        "contact_id": contact_id,
        "job_id": job_id,
        "relevance_score": 8.4,
        "same_company": True,
        "department_relevance": 0.8,
        "role_similarity": 0.9,
        "seniority_fit": 0.7,
        "ranked_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return ContactScore(**fields)


async def test_add_and_get_round_trips_a_contact(session: AsyncSession) -> None:
    repository = ContactRepository(session)
    contact = _contact()

    await repository.add(contact)
    await session.commit()

    fetched = await repository.get(contact.id)
    assert fetched is not None
    assert fetched.full_name == "Jordan Smith"
    assert fetched.email == "jordan@example.com"
    assert fetched.status == ContactStatus.RANKED
    assert fetched.contact_type == ContactType.PRACTITIONER
    assert fetched.user_id == contact.user_id


async def test_list_for_job_returns_only_that_jobs_contacts(session: AsyncSession) -> None:
    repository = ContactRepository(session)
    job_id = JobId(uuid4())
    other_job_id = JobId(uuid4())

    await repository.add(_contact(job_id=job_id, full_name="In Scope"))
    await repository.add(_contact(job_id=other_job_id, full_name="Out Of Scope"))
    await session.commit()

    results = await repository.list_for_job(job_id)
    assert [c.full_name for c in results] == ["In Scope"]


async def test_list_for_job_empty_when_no_contacts(session: AsyncSession) -> None:
    repository = ContactRepository(session)
    assert await repository.list_for_job(JobId(uuid4())) == []


async def test_contact_score_add_and_get_for_contact_round_trips(session: AsyncSession) -> None:
    contact = _contact()
    await ContactRepository(session).add(contact)
    await session.commit()

    score_repository = ContactScoreRepository(session)
    score = _score(contact.id, contact.job_id)
    await score_repository.add(score)
    await session.commit()

    fetched = await score_repository.get_for_contact(contact.id)
    assert fetched is not None
    assert fetched.relevance_score == pytest.approx(8.4)
    assert fetched.same_company is True
    assert fetched.department_relevance == pytest.approx(0.8)
    assert fetched.role_similarity == pytest.approx(0.9)
    assert fetched.seniority_fit == pytest.approx(0.7)


async def test_contact_score_optional_fields_persist_as_none(session: AsyncSession) -> None:
    """Scenario D (persistence slice): missing optional signals persist as
    NULL, not a crash."""
    contact = _contact()
    await ContactRepository(session).add(contact)
    await session.commit()

    score = _score(
        contact.id, contact.job_id, department_relevance=None, seniority_fit=None
    )
    await ContactScoreRepository(session).add(score)
    await session.commit()

    fetched = await ContactScoreRepository(session).get_for_contact(contact.id)
    assert fetched is not None
    assert fetched.department_relevance is None
    assert fetched.seniority_fit is None


async def test_list_contacts_with_relevance_joins_score(session: AsyncSession) -> None:
    job_id = JobId(uuid4())
    contact = _contact(job_id=job_id)
    await ContactRepository(session).add(contact)
    await ContactScoreRepository(session).add(
        _score(contact.id, job_id, relevance_score=6.25)
    )
    await session.commit()

    rows = await list_contacts_with_relevance(session, job_id)
    assert len(rows) == 1
    fetched_contact, relevance_score = rows[0]
    assert fetched_contact.id == contact.id
    assert relevance_score == pytest.approx(6.25)


async def test_list_contacts_with_relevance_handles_missing_score(session: AsyncSession) -> None:
    """A contact with no paired score row (should not happen via the normal
    workflow, but the LEFT JOIN must not crash or drop the contact)."""
    job_id = JobId(uuid4())
    contact = _contact(job_id=job_id)
    await ContactRepository(session).add(contact)
    await session.commit()

    rows = await list_contacts_with_relevance(session, job_id)
    assert len(rows) == 1
    fetched_contact, relevance_score = rows[0]
    assert fetched_contact.id == contact.id
    assert relevance_score is None
