"""Shared fixtures and factories for Tracking Service tests.

Everything here runs against fakes or SQLite in-memory — no PostgreSQL or
Kafka broker is required to run `tests/tracking`. Mirrors
`tests/matching/conftest.py`'s pattern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import tracking.db as db_module
import tracking.events as events_module
from shared.types.dto import (
    ContactRankingResult,
    JobMatchResult,
    NormalizedJob,
    RankedContact,
)
from shared.types.enums import ContactType, JobSourceType, MatchRecommendation
from shared.types.ids import (
    ContactId,
    JobId,
    JobMatchId,
    ProfileId,
    ResumeId,
    UserId,
)


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Domain/event factories
# ---------------------------------------------------------------------------


def make_normalized_job(user_id: UserId, **overrides: object) -> NormalizedJob:
    fields: dict[str, object] = {
        "job_id": JobId(uuid4()),
        "user_id": user_id,
        "company": "Acme Robotics",
        "title": "Senior Mechanical Design Engineer",
        "location": "Remote",
        "description": "Design mechanical subsystems for industrial robots.",
        "extracted_skills": ["CAD", "GD&T"],
        "experience_required": "5+ years",
        "source_type": JobSourceType.MANUAL_URL,
        "source_url": "https://boards.example.com/jobs/1",
        "discovered_at": now(),
    }
    fields.update(overrides)
    return NormalizedJob(**fields)


def make_job_match_result(job_id: JobId, user_id: UserId, **overrides: object) -> JobMatchResult:
    fields: dict[str, object] = {
        "job_match_id": JobMatchId(uuid4()),
        "job_id": job_id,
        "user_id": user_id,
        "selected_profile_id": ProfileId(uuid4()),
        "selected_resume_id": ResumeId(uuid4()),
        "match_score": 0.88,
        "matched_skills": ["CAD", "GD&T"],
        "missing_skills": ["Six Sigma"],
        "recommendation": MatchRecommendation.SHORTLIST,
        "matched_at": now(),
    }
    fields.update(overrides)
    return JobMatchResult(**fields)


def make_contact_ranking_result(
    job_id: JobId, user_id: UserId, *, contacts: list[RankedContact] | None = None, **overrides: object
) -> ContactRankingResult:
    fields: dict[str, object] = {
        "job_id": job_id,
        "user_id": user_id,
        "contacts": contacts if contacts is not None else [],
        "ranked_at": now(),
    }
    fields.update(overrides)
    return ContactRankingResult(**fields)


def make_ranked_contact(**overrides: object) -> RankedContact:
    fields: dict[str, object] = {
        "contact_id": ContactId(uuid4()),
        "full_name": "Jamie Rivera",
        "headline": "Senior Mechanical Engineer at Acme Robotics",
        "contact_type": ContactType.PRACTITIONER,
        "profile_url": "https://example.com/in/jamie-rivera",
        "relevance_score": 8.7,
    }
    fields.update(overrides)
    return RankedContact(**fields)


# ---------------------------------------------------------------------------
# Real (SQLite in-memory) async session
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """An isolated in-memory SQLite DB with this component's own tables
    (`applications`, `application_history`) — mirrors
    `tests/matching/conftest.py`'s `session_factory` fixture.
    """
    pytest.importorskip(
        "aiosqlite", reason="async sqlite driver not installed; DB tests need it"
    )
    from sqlalchemy.ext.asyncio import create_async_engine

    try:
        from infrastructure.database import Base
    except ImportError as exc:  # infrastructure.database's internals raise ImportError
        pytest.skip(f"infrastructure.database.Base not available yet: {exc}")

    from tracking.models import ApplicationHistoryRecord, ApplicationRecord

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            tables=[ApplicationRecord.__table__, ApplicationHistoryRecord.__table__],
        )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as open_session:
        yield open_session


# ---------------------------------------------------------------------------
# Reset module-level DI singletons between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_tracking_singletons() -> Iterator[None]:
    yield
    events_module.set_event_producer(None)
    db_module.set_session_factory(None)


__all__ = [
    "make_contact_ranking_result",
    "make_job_match_result",
    "make_normalized_job",
    "make_ranked_contact",
    "now",
]
