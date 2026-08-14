"""Tests for `matching.repository.JobMatchRepository` /
`JobProcessingStatusRepository` against a real (SQLite in-memory) async
SQLAlchemy session — docs/architecture/database-ownership.md#job_matches
and the `processing_status` "transition ownership" note under #jobs.

Uses the `session_factory`/`session` fixtures from conftest.py.

Scenario H (persistence): JobMatch persisted with correct
selected_profile_id/selected_resume_id, readable back via the repository.
Scenario J (state transitions): only the documented
NORMALIZED -> MATCHED | FAILED transition occurs; no other column touched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, String, Table, Uuid, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from matching.repository import JobMatchRepository, JobProcessingStatusRepository
from shared.types.domain.job_match import JobMatch
from shared.types.dto import ProfileMatchScore
from shared.types.enums import JobProcessingStatus, MatchRecommendation
from shared.types.ids import JobId, JobMatchId, ProfileId, ResumeId, UserId

pytestmark = pytest.mark.asyncio


def _job_match(**overrides: object) -> JobMatch:
    profile_id = ProfileId(uuid4())
    resume_id = ResumeId(uuid4())
    fields: dict[str, object] = {
        "id": JobMatchId(uuid4()),
        "job_id": JobId(uuid4()),
        "user_id": UserId(uuid4()),
        "selected_profile_id": profile_id,
        "selected_resume_id": resume_id,
        "match_score": 0.87,
        "matched_skills": ["CAD", "GD&T"],
        "missing_skills": ["Six Sigma"],
        "recommendation": MatchRecommendation.SHORTLIST,
        "profile_scores": [
            ProfileMatchScore(
                profile_id=profile_id,
                resume_id=resume_id,
                score=0.87,
                matched_skills=["CAD", "GD&T"],
                missing_skills=["Six Sigma"],
            )
        ],
        "matched_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return JobMatch(**fields)


# A local, un-mapped Core `Table` matching the stand-in `jobs` table the
# `session_factory` fixture creates (Uuid `id` column, same type
# `matching.repository`'s own narrow-grant `Table` uses) — used only so
# these tests' own INSERT/SELECT calls go through the same Uuid bind/result
# processing `JobProcessingStatusRepository.mark_status` does, rather than
# a manually-formatted string via raw SQL (SQLite's generic `Uuid` type
# storage format is a SQLAlchemy implementation detail, not a fixed
# 36-character-with-dashes string).
_jobs_table = Table(
    "jobs",
    MetaData(),
    Column("id", Uuid, primary_key=True),
    Column("company", String, nullable=False),
    Column("processing_status", String, nullable=False),
)


# ---------------------------------------------------------------------------
# Scenario H — persistence
# ---------------------------------------------------------------------------


async def test_add_persists_job_match_with_selected_profile_and_resume(
    session: AsyncSession,
) -> None:
    repository = JobMatchRepository(session)
    job_match = _job_match()

    await repository.add(job_match)
    await session.commit()

    fetched = await repository.get(job_match.id)
    assert fetched is not None
    assert fetched.selected_profile_id == job_match.selected_profile_id
    assert fetched.selected_resume_id == job_match.selected_resume_id
    assert fetched.match_score == pytest.approx(0.87)
    assert fetched.matched_skills == ["CAD", "GD&T"]
    assert fetched.missing_skills == ["Six Sigma"]
    assert fetched.recommendation == MatchRecommendation.SHORTLIST
    assert len(fetched.profile_scores) == 1
    assert fetched.profile_scores[0].profile_id == job_match.selected_profile_id


async def test_get_latest_for_job_returns_none_when_no_match_exists(
    session: AsyncSession,
) -> None:
    repository = JobMatchRepository(session)
    assert await repository.get_latest_for_job(JobId(uuid4())) is None


async def test_get_latest_for_job_returns_most_recent_row(session: AsyncSession) -> None:
    repository = JobMatchRepository(session)
    job_id = JobId(uuid4())

    older = _job_match(job_id=job_id, matched_at=datetime(2026, 1, 1, tzinfo=UTC))
    newer = _job_match(job_id=job_id, matched_at=datetime(2026, 6, 1, tzinfo=UTC))
    await repository.add(older)
    await repository.add(newer)
    await session.commit()

    latest = await repository.get_latest_for_job(job_id)
    assert latest is not None
    assert latest.id == newer.id


# ---------------------------------------------------------------------------
# Scenario J — narrow processing_status transition, no other column touched
# ---------------------------------------------------------------------------


async def test_mark_status_updates_only_processing_status_column(
    session: AsyncSession,
) -> None:
    job_id = uuid4()
    await session.execute(
        insert(_jobs_table).values(
            id=job_id, company="Acme Robotics", processing_status="NORMALIZED"
        )
    )
    await session.commit()

    repository = JobProcessingStatusRepository(session)
    await repository.mark_status(JobId(job_id), JobProcessingStatus.MATCHED)
    await session.commit()

    row = (
        await session.execute(select(_jobs_table).where(_jobs_table.c.id == job_id))
    ).one()
    assert row.company == "Acme Robotics"  # untouched
    assert row.processing_status == "MATCHED"


async def test_mark_status_failed_for_no_profiles_available(session: AsyncSession) -> None:
    job_id = uuid4()
    await session.execute(
        insert(_jobs_table).values(
            id=job_id, company="Acme Robotics", processing_status="NORMALIZED"
        )
    )
    await session.commit()

    repository = JobProcessingStatusRepository(session)
    await repository.mark_status(JobId(job_id), JobProcessingStatus.FAILED)
    await session.commit()

    row = (
        await session.execute(select(_jobs_table).where(_jobs_table.c.id == job_id))
    ).one()
    assert row.processing_status == "FAILED"
