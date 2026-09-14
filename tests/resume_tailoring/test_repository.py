"""Tests for `resume_tailoring.repository.TailoredResumeRepository` against
a real (SQLite in-memory) async SQLAlchemy session —
docs/tailored-resume/TECH.md Task 5.

Same shape as `tests/matching/test_repository.py`: an isolated in-memory
database created for exactly this component's own table, so the repository
is exercised through real SQL (ordering, filtering, round-tripping) rather
than through a fake.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from resume_tailoring.models import TailoredResumeRecord
from resume_tailoring.repository import TailoredResumeRepository
from shared.types.ids import JobId, ResumeId, UserId

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """An isolated in-memory SQLite DB holding only `tailored_resumes`.

    Scoped to this component's own table rather than a blanket
    `Base.metadata.create_all()` for the reason
    `tests/matching/conftest.py::session_factory` documents at length:
    `Base.metadata` is one process-wide registry, so what gets created
    would otherwise depend on which other components' `models.py` modules
    an earlier test module in the same run happened to import.
    """
    pytest.importorskip(
        "aiosqlite", reason="async sqlite driver not installed; DB tests need it"
    )
    from infrastructure.database import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all, tables=[TailoredResumeRecord.__table__]
        )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as open_session:
        yield open_session

    await engine.dispose()


def _add_kwargs(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "user_id": UserId(uuid4()),
        "job_id": JobId(uuid4()),
        "source_resume_id": ResumeId(uuid4()),
        "storage_uri": "/tmp/tailored/resume.docx",
        "base_score": 0.62,
        "achieved_score": 0.78,
    }
    fields.update(overrides)
    return fields


async def test_add_then_get_for_job_returns_the_row(session: AsyncSession) -> None:
    repo = TailoredResumeRepository(session)
    user_id = UserId(uuid4())
    job_id = JobId(uuid4())
    resume_id = ResumeId(uuid4())

    created = await repo.add(
        **_add_kwargs(
            user_id=user_id,
            job_id=job_id,
            source_resume_id=resume_id,
            storage_uri="/var/data/tailored_resumes/u/abc_def.docx",
            base_score=0.62,
            achieved_score=0.81,
        )
    )
    fetched = await repo.get_for_job(user_id, job_id)

    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.user_id == user_id
    assert fetched.job_id == job_id
    assert fetched.source_resume_id == resume_id
    assert fetched.storage_uri == "/var/data/tailored_resumes/u/abc_def.docx"
    assert fetched.base_score == pytest.approx(0.62)
    assert fetched.achieved_score == pytest.approx(0.81)
    # `created_at` is minted by the repository, not supplied by the caller.
    assert fetched.created_at is not None


async def test_add_then_get_by_id_returns_the_row(session: AsyncSession) -> None:
    repo = TailoredResumeRepository(session)

    created = await repo.add(**_add_kwargs())
    fetched = await repo.get(created.id)

    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.storage_uri == created.storage_uri


async def test_get_by_unknown_id_returns_none(session: AsyncSession) -> None:
    repo = TailoredResumeRepository(session)

    assert await repo.get(uuid4()) is None


async def test_get_for_job_without_a_tailored_resume_returns_none(
    session: AsyncSession,
) -> None:
    """"Not tailored yet" is an ordinary answer, not an error."""
    repo = TailoredResumeRepository(session)
    await repo.add(**_add_kwargs())  # an unrelated row exists

    assert await repo.get_for_job(UserId(uuid4()), JobId(uuid4())) is None


async def test_two_jobs_for_the_same_user_do_not_collide(session: AsyncSession) -> None:
    repo = TailoredResumeRepository(session)
    user_id = UserId(uuid4())
    first_job = JobId(uuid4())
    second_job = JobId(uuid4())

    first = await repo.add(
        **_add_kwargs(user_id=user_id, job_id=first_job, storage_uri="/data/first.docx")
    )
    second = await repo.add(
        **_add_kwargs(user_id=user_id, job_id=second_job, storage_uri="/data/second.docx")
    )

    fetched_first = await repo.get_for_job(user_id, first_job)
    fetched_second = await repo.get_for_job(user_id, second_job)

    assert fetched_first is not None and fetched_second is not None
    assert fetched_first.id == first.id
    assert fetched_second.id == second.id
    assert fetched_first.storage_uri == "/data/first.docx"
    assert fetched_second.storage_uri == "/data/second.docx"


async def test_get_for_job_is_scoped_to_the_requesting_user(session: AsyncSession) -> None:
    """One user's row is never served for another user's request, even if the
    same `job_id` somehow appears under both.
    """
    repo = TailoredResumeRepository(session)
    job_id = JobId(uuid4())
    owner = UserId(uuid4())
    other = UserId(uuid4())
    await repo.add(**_add_kwargs(user_id=owner, job_id=job_id))

    assert await repo.get_for_job(other, job_id) is None
    assert await repo.get_for_job(owner, job_id) is not None


async def test_get_for_job_returns_the_most_recent_row(session: AsyncSession) -> None:
    """Regeneration scenario: nothing calls this twice today, but the schema
    permits a second row for the same job, so the newest must win.

    `created_at` is passed explicitly (rather than relying on two
    `datetime.now()` calls microseconds apart) so the assertion tests the
    `ORDER BY created_at DESC` logic itself, not the system clock's
    resolution. The older row is inserted *second* so a repository that
    silently returned insertion order would fail this test.
    """
    repo = TailoredResumeRepository(session)
    user_id = UserId(uuid4())
    job_id = JobId(uuid4())

    newer = await repo.add(
        **_add_kwargs(
            user_id=user_id,
            job_id=job_id,
            storage_uri="/data/regenerated.docx",
            achieved_score=0.91,
            created_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        )
    )
    older = await repo.add(
        **_add_kwargs(
            user_id=user_id,
            job_id=job_id,
            storage_uri="/data/original.docx",
            achieved_score=0.78,
            created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        )
    )

    fetched = await repo.get_for_job(user_id, job_id)

    assert fetched is not None
    assert fetched.id == newer.id
    assert fetched.id != older.id
    assert fetched.storage_uri == "/data/regenerated.docx"
    assert fetched.achieved_score == pytest.approx(0.91)
    # Both rows are still there — this table is append-only, never updated.
    assert await repo.get(older.id) is not None
