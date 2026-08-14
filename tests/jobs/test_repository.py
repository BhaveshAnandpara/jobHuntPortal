"""Tests for `jobs.repository.JobRepository` / `JobSourceRepository`
against a real (SQLite in-memory) async SQLAlchemy session —
docs/architecture/database-ownership.md#jobs and #job_sources.

Uses the `session` fixture from conftest.py, which skips (rather than
erroring) when `infrastructure.database.Base` isn't importable or the
`aiosqlite` driver isn't installed.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shared.types.domain.job import Job
from shared.types.domain.job_source import JobSource
from shared.types.enums import JobProcessingStatus, JobSourceType
from shared.types.ids import JobId, JobSourceId, UserId

pytestmark = pytest.mark.asyncio


def _job(user_id: UserId, **overrides: object) -> Job:
    fields: dict[str, object] = {
        "id": JobId(uuid4()),
        "user_id": user_id,
        "source_type": JobSourceType.MANUAL_URL,
        "source_id": None,
        "source_url": "https://boards.example.com/jobs/1",
        "company": "Acme Robotics",
        "title": "Senior Mechanical Engineer",
        "location": "Remote",
        "description_raw": "Design and validate mechanical subsystems.",
        "extracted_skills": ["CAD"],
        "experience_required": "5+ years",
        "processing_status": JobProcessingStatus.NORMALIZED,
        "discovered_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return Job(**fields)


def _source(user_id: UserId, **overrides: object) -> JobSource:
    fields: dict[str, object] = {
        "id": JobSourceId(uuid4()),
        "user_id": user_id,
        "name": "LinkedIn - Mechanical roles",
        "type": JobSourceType.LINKEDIN,
        "query_config": {"keywords": ["Mechanical Engineer"]},
        "enabled": True,
        "last_run_at": None,
    }
    fields.update(overrides)
    return JobSource(**fields)


async def test_insert_manual_persists_and_is_found_by_source_url(
    session: AsyncSession,
) -> None:
    from jobs.repository import JobRepository

    repository = JobRepository(session)
    user_id = UserId(uuid4())
    job = _job(user_id)

    await repository.insert_manual(job)
    await session.commit()

    found = await repository.find_by_source_url(user_id, job.source_url)
    assert found is not None
    assert found.id == job.id
    assert found.company == job.company


async def test_insert_manual_rejects_non_manual_source_type(session: AsyncSession) -> None:
    from jobs.repository import JobRepository

    repository = JobRepository(session)
    job = _job(UserId(uuid4()), source_type=JobSourceType.LINKEDIN)

    with pytest.raises(ValueError):
        await repository.insert_manual(job)


async def test_insert_discovered_rejects_manual_source_type(session: AsyncSession) -> None:
    from jobs.repository import JobRepository

    repository = JobRepository(session)
    job = _job(UserId(uuid4()))  # defaults to MANUAL_URL

    with pytest.raises(ValueError):
        await repository.insert_discovered(job)


async def test_insert_discovered_persists_automatic_source(session: AsyncSession) -> None:
    from jobs.repository import JobRepository

    repository = JobRepository(session)
    user_id = UserId(uuid4())
    job = _job(
        user_id,
        source_type=JobSourceType.LINKEDIN,
        source_id=None,  # avoid a real FK dependency in this isolated test
        source_url="https://boards.example.com/jobs/2",
    )

    await repository.insert_discovered(job)
    await session.commit()

    found = await repository.find_by_source_url(user_id, job.source_url)
    assert found is not None
    assert found.source_type is JobSourceType.LINKEDIN


async def test_find_by_source_url_is_scoped_per_user(session: AsyncSession) -> None:
    from jobs.repository import JobRepository

    repository = JobRepository(session)
    url = "https://boards.example.com/jobs/shared"
    user_a = UserId(uuid4())
    user_b = UserId(uuid4())

    await repository.insert_manual(_job(user_a, source_url=url))
    await session.commit()

    assert await repository.find_by_source_url(user_a, url) is not None
    assert await repository.find_by_source_url(user_b, url) is None


async def test_get_returns_job_by_id(session: AsyncSession) -> None:
    from jobs.repository import JobRepository

    repository = JobRepository(session)
    user_id = UserId(uuid4())
    job = _job(user_id)

    await repository.insert_manual(job)
    await session.commit()

    found = await repository.get(job.id)
    assert found is not None
    assert found.company == job.company
    assert found.title == job.title


async def test_get_returns_none_when_absent(session: AsyncSession) -> None:
    from jobs.repository import JobRepository

    repository = JobRepository(session)
    assert await repository.get(JobId(uuid4())) is None


async def test_find_by_source_url_returns_none_when_absent(session: AsyncSession) -> None:
    from jobs.repository import JobRepository

    repository = JobRepository(session)
    assert (
        await repository.find_by_source_url(
            UserId(uuid4()), "https://boards.example.com/nope"
        )
        is None
    )


async def test_job_source_repository_insert_get_list(session: AsyncSession) -> None:
    from jobs.repository import JobSourceRepository

    repository = JobSourceRepository(session)
    user_id = UserId(uuid4())
    source = _source(user_id)

    await repository.insert(source)
    await session.commit()

    fetched = await repository.get(source.id)
    assert fetched is not None
    assert fetched.name == source.name

    listed = await repository.list_for_user(user_id)
    assert [s.id for s in listed] == [source.id]


async def test_job_source_repository_list_enabled_excludes_disabled(
    session: AsyncSession,
) -> None:
    from jobs.repository import JobSourceRepository

    repository = JobSourceRepository(session)
    user_id = UserId(uuid4())
    enabled = _source(user_id, name="enabled-source", enabled=True)
    disabled = _source(user_id, name="disabled-source", enabled=False)

    await repository.insert(enabled)
    await repository.insert(disabled)
    await session.commit()

    listed = await repository.list_enabled()
    assert [s.id for s in listed] == [enabled.id]


async def test_job_source_repository_mark_run_updates_timestamp(
    session: AsyncSession,
) -> None:
    from jobs.repository import JobSourceRepository

    repository = JobSourceRepository(session)
    user_id = UserId(uuid4())
    source = _source(user_id)
    await repository.insert(source)
    await session.commit()

    run_at = datetime.now(UTC)
    await repository.mark_run(source.id, run_at)
    await session.commit()

    fetched = await repository.get(source.id)
    assert fetched is not None
    assert fetched.last_run_at is not None


async def test_job_source_repository_mark_run_unknown_source_raises(
    session: AsyncSession,
) -> None:
    from jobs.repository import JobSourceRepository

    repository = JobSourceRepository(session)
    with pytest.raises(ValueError):
        await repository.mark_run(JobSourceId(uuid4()), datetime.now(UTC))
