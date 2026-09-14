"""Download filenames: sanitization, composition, and versioning.

Three layers, tested at the level each one actually owns:

* `sanitize_filename_part` / `build_document_filename` — pure functions,
  driven directly (they are what decides what a browser saves)
* `get_document_naming` — the router's only cross-component read, exercised
  against **real** `jobs` and `candidate_profiles` rows rather than a fake,
  because the whole point of that dependency is that those two reads work
* `TailoredResumeRepository.count_for_job` — the version number's source

The route-level behavior (that the resolved name actually reaches
`Content-Disposition`, and that a regeneration downloads as `_v2`) lives in
`test_api.py`, which substitutes this dependency so it can keep its database
scoped to `tailored_resumes` alone.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from resume_tailoring.api import (
    DocumentNaming,
    build_document_filename,
    get_document_naming,
    sanitize_filename_part,
)
from resume_tailoring.models import TailoredResume, TailoredResumeRecord
from resume_tailoring.repository import TailoredResumeRepository
from shared.types.dto import ContactInfo
from shared.types.ids import JobId, ResumeId, UserId
from tests.resume_tailoring.conftest import make_candidate_profile, make_job

# No module-level `pytest.mark.asyncio` here (unlike the other files in this
# package): the filename functions are synchronous, so the marker is applied
# per async test instead.


# ---------------------------------------------------------------------------
# sanitize_filename_part
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Aoife Devlin", "Aoife_Devlin"),
        ("Riverside General Hospital", "Riverside_General_Hospital"),
        ("ICU Staff Nurse", "ICU_Staff_Nurse"),
        # Casing is preserved — it is the only thing keeping this readable.
        ("PwC", "PwC"),
        # Accents fold to their ASCII base rather than vanishing entirely.
        ("Zoë Müller", "Zoe_Muller"),
        # Runs of punctuation collapse to a single underscore, and the ends
        # are trimmed, so no doubled or dangling separators appear.
        ("Nurse (ICU) — Nights!!", "Nurse_ICU_Nights"),
        ("  Senior   Engineer  ", "Senior_Engineer"),
        ("Smith & Sons, Ltd.", "Smith_Sons_Ltd"),
        (None, ""),
        ("", ""),
        ("   ", ""),
    ],
)
def test_sanitize_filename_part(raw: str | None, expected: str) -> None:
    assert sanitize_filename_part(raw) == expected


@pytest.mark.parametrize(
    "hostile",
    [
        "../../etc/passwd",
        r"..\..\windows\system32",
        'name" ; rm -rf /',
        "line\r\nbreak",
        "a/b/c",
    ],
)
def test_sanitize_filename_part_neutralizes_path_and_header_injection(
    hostile: str,
) -> None:
    """Everything in a filename now comes from user data, so the rule is an
    allowlist: no separator, quote, or newline can survive into the
    `Content-Disposition` header or onto a client's filesystem."""
    cleaned = sanitize_filename_part(hostile)

    assert all(character.isalnum() or character == "_" for character in cleaned)
    for forbidden in ("/", "\\", "..", '"', "\r", "\n", ";", " "):
        assert forbidden not in cleaned


def test_sanitize_filename_part_drops_a_value_with_no_ascii_letters() -> None:
    """A purely non-Latin value yields nothing rather than mojibake — the
    caller then omits the segment like any other absent field."""
    assert sanitize_filename_part("株式会社") == ""


# ---------------------------------------------------------------------------
# build_document_filename
# ---------------------------------------------------------------------------

JOB_ID = uuid4()


def test_filename_is_name_company_role_resume() -> None:
    assert (
        build_document_filename(
            job_id=JOB_ID,
            full_name="Aoife Devlin",
            company="Riverside General Hospital",
            role="ICU Staff Nurse",
        )
        == "Aoife_Devlin_Riverside_General_Hospital_ICU_Staff_Nurse_Resume.docx"
    )


@pytest.mark.parametrize("version", [0, 1])
def test_first_version_carries_no_suffix(version: int) -> None:
    """Version 1 is the first generation; 0 is "the caller could not count",
    which must not produce `_v0`."""
    assert build_document_filename(
        job_id=JOB_ID, full_name="Aoife Devlin", version=version
    ) == "Aoife_Devlin_Resume.docx"


@pytest.mark.parametrize(("version", "suffix"), [(2, "_v2"), (3, "_v3"), (11, "_v11")])
def test_regenerations_are_versioned(version: int, suffix: str) -> None:
    assert build_document_filename(
        job_id=JOB_ID,
        full_name="Aoife Devlin",
        company="Riverside General Hospital",
        role="ICU Staff Nurse",
        version=version,
    ) == f"Aoife_Devlin_Riverside_General_Hospital_ICU_Staff_Nurse_Resume{suffix}.docx"


def test_absent_segments_are_dropped_without_dangling_separators() -> None:
    """A missing company must not become a placeholder, an empty segment, or
    a doubled underscore — the same omission rule the document renderer
    applies to contact details."""
    filename = build_document_filename(
        job_id=JOB_ID, full_name="Aoife Devlin", company=None, role="ICU Staff Nurse"
    )

    assert filename == "Aoife_Devlin_ICU_Staff_Nurse_Resume.docx"
    assert "__" not in filename


def test_filename_falls_back_to_the_job_id_when_nothing_resolves() -> None:
    """Ugly, but unique and never colliding across jobs — and, critically,
    never a file called `_Resume.docx` or one carrying invented text."""
    assert (
        build_document_filename(job_id=JOB_ID)
        == f"tailored-resume-{JOB_ID}.docx"
    )


def test_filename_is_always_header_safe() -> None:
    filename = build_document_filename(
        job_id=JOB_ID,
        full_name='Zoë "Zed" Müller',
        company="Smith & Sons, Ltd.",
        role="Nurse (ICU) / Nights",
        version=2,
    )

    assert filename == "Zoe_Zed_Muller_Smith_Sons_Ltd_Nurse_ICU_Nights_Resume_v2.docx"
    assert '"' not in filename


# ---------------------------------------------------------------------------
# get_document_naming — against real rows
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """An in-memory database carrying the tables the naming path touches:
    this component's own, plus the two it reads and the tables their
    declared foreign keys point at.

    Listed explicitly rather than via a blanket `Base.metadata.create_all()`
    for the reason `tests/resume_tailoring/test_repository.py` documents:
    `Base.metadata` is one process-wide registry, so a blanket create would
    depend on which other components' `models.py` an earlier test module
    happened to import.
    """
    pytest.importorskip("aiosqlite", reason="async sqlite driver not installed")
    from infrastructure.database import Base
    from jobs.models import JobRecord, JobSourceRecord
    from profiles.models import CandidateProfileRecord, ResumeRecord
    from users.models import UserRecord

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            tables=[
                TailoredResumeRecord.__table__,
                JobRecord.__table__,
                JobSourceRecord.__table__,
                CandidateProfileRecord.__table__,
                ResumeRecord.__table__,
                UserRecord.__table__,
            ],
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as open_session:
        yield open_session
    await engine.dispose()


def _record(*, user_id: UserId, job_id: JobId, resume_id: ResumeId) -> TailoredResume:
    from datetime import UTC, datetime

    return TailoredResume(
        id=uuid4(),
        user_id=user_id,
        job_id=job_id,
        source_resume_id=resume_id,
        storage_uri="unused-by-naming",
        base_score=0.62,
        achieved_score=0.81,
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_naming_reads_the_candidate_name_and_the_jobs_company_and_role(
    session: AsyncSession,
) -> None:
    from jobs.repository import JobRepository
    from profiles.repository import CandidateProfileRepository

    owner = UserId(uuid4())
    job = make_job(owner)
    profile = make_candidate_profile(owner)
    await JobRepository(session).insert_manual(job)
    await CandidateProfileRepository(session).add(profile)
    await session.flush()

    resolve = get_document_naming(session)
    naming = await resolve(
        _record(user_id=owner, job_id=job.id, resume_id=profile.resume_id)
    )

    assert naming == DocumentNaming(
        full_name="Aoife Devlin",
        company="Riverside General Hospital",
        role="ICU Staff Nurse",
    )
    assert (
        build_document_filename(
            job_id=job.id,
            full_name=naming.full_name,
            company=naming.company,
            role=naming.role,
        )
        == "Aoife_Devlin_Riverside_General_Hospital_ICU_Staff_Nurse_Resume.docx"
    )


@pytest.mark.asyncio
async def test_naming_reports_nothing_when_the_rows_are_gone(
    session: AsyncSession,
) -> None:
    """A deleted job and an unknown resume are ordinary answers, not errors:
    the filename simply loses those segments."""
    resolve = get_document_naming(session)

    naming = await resolve(
        _record(
            user_id=UserId(uuid4()),
            job_id=JobId(uuid4()),
            resume_id=ResumeId(uuid4()),
        )
    )

    assert naming == DocumentNaming()


@pytest.mark.asyncio
async def test_naming_tolerates_a_profile_with_no_contact_details(
    session: AsyncSession,
) -> None:
    from jobs.repository import JobRepository
    from profiles.repository import CandidateProfileRepository

    owner = UserId(uuid4())
    job = make_job(owner)
    profile = make_candidate_profile(owner, contact=None)
    await JobRepository(session).insert_manual(job)
    await CandidateProfileRepository(session).add(profile)
    await session.flush()

    resolve = get_document_naming(session)
    naming = await resolve(
        _record(user_id=owner, job_id=job.id, resume_id=profile.resume_id)
    )

    assert naming.full_name is None
    assert naming.company == "Riverside General Hospital"


@pytest.mark.asyncio
async def test_naming_does_not_invent_a_name_from_an_unnamed_contact(
    session: AsyncSession,
) -> None:
    from jobs.repository import JobRepository
    from profiles.repository import CandidateProfileRepository

    owner = UserId(uuid4())
    job = make_job(owner)
    profile = make_candidate_profile(
        owner, contact=ContactInfo(email="aoife.devlin@example.com")
    )
    await JobRepository(session).insert_manual(job)
    await CandidateProfileRepository(session).add(profile)
    await session.flush()

    naming = await get_document_naming(session)(
        _record(user_id=owner, job_id=job.id, resume_id=profile.resume_id)
    )

    assert naming.full_name is None
    filename = build_document_filename(
        job_id=job.id,
        full_name=naming.full_name,
        company=naming.company,
        role=naming.role,
    )
    assert filename == "Riverside_General_Hospital_ICU_Staff_Nurse_Resume.docx"
    assert "aoife" not in filename.lower()


# ---------------------------------------------------------------------------
# count_for_job — the version number
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_for_job_counts_this_users_generations_only(
    session: AsyncSession,
) -> None:
    repository = TailoredResumeRepository(session)
    owner = UserId(uuid4())
    other = UserId(uuid4())
    job_id = JobId(uuid4())
    other_job = JobId(uuid4())
    resume_id = ResumeId(uuid4())

    assert await repository.count_for_job(owner, job_id) == 0

    for _ in range(3):
        await repository.add(
            user_id=owner,
            job_id=job_id,
            source_resume_id=resume_id,
            storage_uri="x",
            base_score=0.6,
            achieved_score=0.8,
        )
    # Neither another job nor another user's row may shift the count.
    await repository.add(
        user_id=owner,
        job_id=other_job,
        source_resume_id=resume_id,
        storage_uri="x",
        base_score=0.6,
        achieved_score=0.8,
    )
    await repository.add(
        user_id=other,
        job_id=job_id,
        source_resume_id=resume_id,
        storage_uri="x",
        base_score=0.6,
        achieved_score=0.8,
    )

    assert await repository.count_for_job(owner, job_id) == 3
    assert await repository.count_for_job(other, job_id) == 1
    assert await repository.count_for_job(other, other_job) == 0
