"""Tests for profiles/repository.py against a real SQLAlchemy session
(SQLite in-memory), mirroring tests/users/conftest.py's pattern.

Both previously-documented blockers are resolved as of this pass:
`infrastructure.database` now re-exports `Base`, and `aiosqlite` is
installed as a dev dependency (added centrally in pyproject.toml, outside
this component's ownership). This module now runs rather than erroring at
collection/fixture-setup time.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from infrastructure.database import Base
from profiles.models import (  # noqa: F401 — registers tables
    CandidateProfileRecord,
    ResumeRecord,
)
from profiles.repository import (
    CandidateProfileRepository,
    ResumeRepository,
    to_resume_profile,
)
from shared.types.domain.candidate_profile import CandidateProfile
from shared.types.domain.resume import Resume
from shared.types.dto import ContactInfo, WorkExperienceEntry
from shared.types.enums import ProfileStatus, ResumeStatus
from shared.types.ids import ProfileId, ResumeId, UserId
from users.models import UserRecord  # noqa: F401 — registers the users FK target table


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as open_session:
        yield open_session

    await engine.dispose()


def _make_resume(**overrides) -> Resume:
    defaults = {
        "id": ResumeId(uuid4()),
        "user_id": UserId(uuid4()),
        "file_name": "resume.txt",
        "storage_uri": "/tmp/resume.txt",
        "raw_text": None,
        "status": ResumeStatus.UPLOADED,
        "uploaded_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return Resume(**defaults)


def _make_profile(resume: Resume, **overrides) -> CandidateProfile:
    defaults = {
        "id": ProfileId(uuid4()),
        "user_id": resume.user_id,
        "resume_id": resume.id,
        "title": "Data Analyst",
        "status": ProfileStatus.ACTIVE,
        "generated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return CandidateProfile(**defaults)


def _dump_naive(model: Resume | CandidateProfile) -> dict[str, Any]:
    """`model.model_dump()`, with every `datetime` field's `tzinfo`
    stripped before comparison.

    SQLite has no native timezone-aware datetime type, so SQLAlchemy's
    `DateTime(timezone=True)` columns (profiles/models.py) round-trip
    values as naive datetimes even though what was written was
    timezone-aware — a well-known SQLite dialect limitation, not a
    ResumeRepository/CandidateProfileRepository defect. Comparing on the
    naive wall-clock value is what the column type can actually guarantee
    round-trips; PostgreSQL (the real target per about_project.md) does
    preserve tzinfo.
    """
    data = model.model_dump()
    for key, value in data.items():
        if isinstance(value, datetime):
            data[key] = value.replace(tzinfo=None)
    return data


@pytest.mark.asyncio
async def test_resume_add_and_get_round_trips(session: AsyncSession) -> None:
    repo = ResumeRepository(session)
    resume = _make_resume()

    await repo.add(resume)
    fetched = await repo.get(resume.id)

    assert fetched is not None
    assert _dump_naive(fetched) == _dump_naive(resume)


@pytest.mark.asyncio
async def test_resume_list_for_user_scoped_and_ordered(session: AsyncSession) -> None:
    repo = ResumeRepository(session)
    user_id = UserId(uuid4())
    older = _make_resume(user_id=user_id, uploaded_at=datetime(2024, 1, 1, tzinfo=UTC))
    newer = _make_resume(user_id=user_id, uploaded_at=datetime(2024, 6, 1, tzinfo=UTC))
    other_user = _make_resume()

    await repo.add(older)
    await repo.add(newer)
    await repo.add(other_user)

    result = await repo.list_for_user(user_id)

    assert [r.id for r in result] == [newer.id, older.id]


@pytest.mark.asyncio
async def test_resume_mark_parsed_and_mark_parse_failed(session: AsyncSession) -> None:
    repo = ResumeRepository(session)
    resume = _make_resume(status=ResumeStatus.PARSING)
    await repo.add(resume)

    parsed = await repo.mark_parsed(resume.id, "extracted text", datetime.now(UTC))
    assert parsed is not None
    assert parsed.status == ResumeStatus.PARSED
    assert parsed.raw_text == "extracted text"

    failed_target = _make_resume(status=ResumeStatus.PARSING)
    await repo.add(failed_target)
    failed = await repo.mark_parse_failed(failed_target.id, "boom")
    assert failed is not None
    assert failed.status == ResumeStatus.PARSE_FAILED
    assert failed.parse_error == "boom"


@pytest.mark.asyncio
async def test_resume_archive(session: AsyncSession) -> None:
    repo = ResumeRepository(session)
    resume = _make_resume(status=ResumeStatus.PARSED)
    await repo.add(resume)

    archived = await repo.archive(resume.id)

    assert archived is not None
    assert archived.status == ResumeStatus.ARCHIVED


@pytest.mark.asyncio
async def test_candidate_profile_add_get_and_archive_for_resume(session: AsyncSession) -> None:
    resume_repo = ResumeRepository(session)
    profile_repo = CandidateProfileRepository(session)

    resume = _make_resume(status=ResumeStatus.PARSED)
    await resume_repo.add(resume)
    profile = _make_profile(resume)
    await profile_repo.add(profile)

    fetched = await profile_repo.get(profile.id)
    assert fetched is not None
    assert _dump_naive(fetched) == _dump_naive(profile)

    by_resume = await profile_repo.get_by_resume(resume.id)
    assert by_resume is not None
    assert _dump_naive(by_resume) == _dump_naive(profile)

    archived = await profile_repo.archive_for_resume(resume.id)
    assert archived is not None
    assert archived.status == ProfileStatus.ARCHIVED


@pytest.mark.asyncio
async def test_candidate_profile_list_for_user(session: AsyncSession) -> None:
    profile_repo = CandidateProfileRepository(session)
    resume_repo = ResumeRepository(session)
    user_id = UserId(uuid4())

    resume_a = _make_resume(user_id=user_id)
    resume_b = _make_resume(user_id=user_id)
    await resume_repo.add(resume_a)
    await resume_repo.add(resume_b)
    profile_a = _make_profile(resume_a, generated_at=datetime(2024, 1, 1, tzinfo=UTC))
    profile_b = _make_profile(resume_b, generated_at=datetime(2024, 6, 1, tzinfo=UTC))
    await profile_repo.add(profile_a)
    await profile_repo.add(profile_b)

    result = await profile_repo.list_for_user(user_id)

    assert [p.id for p in result] == [profile_b.id, profile_a.id]


def test_to_resume_profile_is_a_pure_projection() -> None:
    resume = _make_resume()
    profile = _make_profile(resume, skills=["SQL", "Tableau"], target_roles=["Data Analyst"])

    result = to_resume_profile(profile)

    assert result.profile_id == profile.id
    assert result.resume_id == profile.resume_id
    assert result.user_id == profile.user_id
    assert result.skills == ["SQL", "Tableau"]
    assert result.target_roles == ["Data Analyst"]
    assert result.status == ProfileStatus.ACTIVE


# ---------------------------------------------------------------------------
# contact / work_history JSON columns (docs/tailored-resume/TECH.md Task 1)
# ---------------------------------------------------------------------------

_CONTACT = ContactInfo(
    full_name="Priya Ramesh",
    email="priya.ramesh@example.com",
    phone="+91 98765 43210",
    location="Chennai, Tamil Nadu, India",
)

_WORK_HISTORY = [
    WorkExperienceEntry(
        company="Sundara Logistics Pvt Ltd",
        title="Regional Operations Manager",
        start_date="March 2021",
        end_date=None,  # current role
        bullets=["Run three distribution centres.", "Cut dispatch turnaround by 39%."],
    ),
    WorkExperienceEntry(
        company="Kaveri Freight Services",
        title="Warehouse Supervisor",
        start_date="June 2017",
        end_date="February 2021",
        bullets=["Owned inbound receiving for a 90,000 sq ft facility."],
    ),
]


@pytest.mark.asyncio
async def test_candidate_profile_contact_and_work_history_round_trip(
    session: AsyncSession,
) -> None:
    resume_repo = ResumeRepository(session)
    profile_repo = CandidateProfileRepository(session)

    resume = _make_resume(status=ResumeStatus.PARSED)
    await resume_repo.add(resume)
    profile = _make_profile(resume, contact=_CONTACT, work_history=_WORK_HISTORY)
    await profile_repo.add(profile)

    session.expunge_all()  # force a real read back out of the JSON columns
    fetched = await profile_repo.get(profile.id)

    assert fetched is not None
    assert fetched.contact == _CONTACT
    assert fetched.work_history == _WORK_HISTORY
    # end_date None (current role) must survive the JSON round trip as None,
    # not as the string "None" or a missing key
    assert fetched.work_history[0].end_date is None
    assert _dump_naive(fetched) == _dump_naive(profile)


@pytest.mark.asyncio
async def test_candidate_profile_without_contact_round_trips_as_none(
    session: AsyncSession,
) -> None:
    """A profile from a resume with no discoverable contact details stores
    NULL and reads back as `None` — never an empty `ContactInfo`, which
    would falsely read as "we have contact details, they're all blank"."""
    resume_repo = ResumeRepository(session)
    profile_repo = CandidateProfileRepository(session)

    resume = _make_resume(status=ResumeStatus.PARSED)
    await resume_repo.add(resume)
    profile = _make_profile(resume)  # no contact, no work history
    await profile_repo.add(profile)

    session.expunge_all()
    fetched = await profile_repo.get(profile.id)

    assert fetched is not None
    assert fetched.contact is None
    assert fetched.work_history == []


def test_to_resume_profile_carries_contact_and_work_history() -> None:
    """Job Matching consumes `ResumeProfile`, so the projection must carry
    the new fields too — the two shapes stay field-for-field in sync."""
    resume = _make_resume()
    profile = _make_profile(resume, contact=_CONTACT, work_history=_WORK_HISTORY)

    result = to_resume_profile(profile)

    assert result.contact == _CONTACT
    assert result.work_history == _WORK_HISTORY
