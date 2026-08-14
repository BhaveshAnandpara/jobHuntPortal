"""Shared fixtures and fakes for Job Ingestion + Job Discovery Service
tests.

Everything here runs against fakes or SQLite in-memory — no PostgreSQL,
Kafka broker, LLM, or browser is required to run `tests/jobs`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from jobs.ingestion.extraction import ExtractedJobFields
from shared.types.domain.job import Job
from shared.types.domain.job_source import JobSource
from shared.types.domain.user_preferences import UserPreferences
from shared.types.dto import NormalizedJob, ResumeProfile
from shared.types.enums import JobProcessingStatus, JobSourceType, ProfileStatus
from shared.types.ids import (
    JobId,
    JobSourceId,
    ProfileId,
    ResumeId,
    UserId,
    UserPreferencesId,
)


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Real (SQLite in-memory) async session — used only by tests/jobs/test_repository.py
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    pytest.importorskip(
        "aiosqlite", reason="async sqlite driver not installed; repository tests need it"
    )
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    try:
        from infrastructure.database import Base
    except ImportError as exc:  # infrastructure.database's internals raise ImportError
        pytest.skip(f"infrastructure.database.Base not available yet: {exc}")

    from jobs.models import JobRecord, JobSourceRecord  # noqa: F401 - registers tables

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as open_session:
        yield open_session

    await engine.dispose()


# ---------------------------------------------------------------------------
# Fakes for PageFetcher / StructuredExtractor (extraction.py's DI seam)
# ---------------------------------------------------------------------------


class FakePageFetcher:
    """`PageFetcher` fake keyed by URL. `errors[url]` raised instead of a
    lookup when present; `pages.get(url, "")` returned otherwise.
    """

    def __init__(
        self,
        pages: dict[str, str] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.pages = pages or {}
        self.errors = errors or {}
        self.requested: list[str] = []

    async def fetch_page(self, url: str) -> str:
        self.requested.append(url)
        if url in self.errors:
            raise self.errors[url]
        return self.pages.get(url, "")


class FakeExtractor:
    """`StructuredExtractor` fake. Results are matched by substring against
    the rendered prompt (which embeds the fetched page content), so keying
    by the same content string used in a `FakePageFetcher.pages` value
    routes the right result to the right posting.
    """

    def __init__(
        self,
        by_content: dict[str, BaseModel | Exception] | None = None,
        default: BaseModel | Exception | None = None,
    ) -> None:
        self._by_content = by_content or {}
        self._default = default
        self.prompts: list[str] = []

    async def extract(self, prompt: str, schema: type[BaseModel]) -> BaseModel:
        self.prompts.append(prompt)
        for content, result in self._by_content.items():
            if content in prompt:
                if isinstance(result, Exception):
                    raise result
                return result
        if self._default is not None:
            if isinstance(self._default, Exception):
                raise self._default
            return self._default
        raise AssertionError(f"FakeExtractor has no scripted result for prompt: {prompt[:120]!r}")


def make_extracted_fields(**overrides: object) -> ExtractedJobFields:
    fields = {
        "company": "Acme Robotics",
        "title": "Senior Mechanical Engineer",
        "location": "Remote",
        "description": "Design and validate mechanical subsystems.",
        "extracted_skills": ["CAD", "GD&T"],
        "experience_required": "5+ years",
    }
    fields.update(overrides)
    return ExtractedJobFields(**fields)


# ---------------------------------------------------------------------------
# Fake repositories (in-memory, no DB) — for pure orchestration tests
# ---------------------------------------------------------------------------


class FakeJobRepository:
    def __init__(self, jobs: list[Job] | None = None) -> None:
        self.jobs: list[Job] = list(jobs or [])
        self.insert_manual_calls = 0
        self.insert_discovered_calls = 0

    async def get(self, job_id: JobId) -> Job | None:
        for job in self.jobs:
            if job.id == job_id:
                return job
        return None

    async def find_by_source_url(self, user_id: UserId, source_url: str) -> Job | None:
        for job in self.jobs:
            if job.user_id == user_id and job.source_url == source_url:
                return job
        return None

    async def insert_manual(self, job: Job) -> None:
        if job.source_type is not JobSourceType.MANUAL_URL:
            raise ValueError("FakeJobRepository.insert_manual requires MANUAL_URL")
        self.jobs.append(job)
        self.insert_manual_calls += 1

    async def insert_discovered(self, job: Job) -> None:
        if job.source_type is JobSourceType.MANUAL_URL:
            raise ValueError("FakeJobRepository.insert_discovered forbids MANUAL_URL")
        self.jobs.append(job)
        self.insert_discovered_calls += 1


class FakeJobSourceRepository:
    def __init__(self, sources: list[JobSource] | None = None) -> None:
        self.sources: dict[JobSourceId, JobSource] = {s.id: s for s in (sources or [])}
        self.mark_run_calls: list[tuple[JobSourceId, datetime]] = []

    async def insert(self, source: JobSource) -> None:
        self.sources[source.id] = source

    async def get(self, source_id: JobSourceId) -> JobSource | None:
        return self.sources.get(source_id)

    async def list_for_user(self, user_id: UserId) -> list[JobSource]:
        return [s for s in self.sources.values() if s.user_id == user_id]

    async def list_enabled(self) -> list[JobSource]:
        return [s for s in self.sources.values() if s.enabled]

    async def mark_run(self, source_id: JobSourceId, run_at: datetime) -> None:
        if source_id not in self.sources:
            raise ValueError(f"unknown job source {source_id}")
        self.mark_run_calls.append((source_id, run_at))
        self.sources[source_id] = self.sources[source_id].model_copy(
            update={"last_run_at": run_at}
        )


class RecordingPublisher:
    """A trivial `PublishFn` recording every `NormalizedJob` it was called
    with, for tests that don't need a real Kafka round-trip.
    """

    def __init__(self) -> None:
        self.published: list[NormalizedJob] = []

    def __call__(self, payload: NormalizedJob) -> NormalizedJob:
        self.published.append(payload)
        return payload


# ---------------------------------------------------------------------------
# Fakes for Job Discovery Service's runtime API + job-board search clients
# ---------------------------------------------------------------------------


class FakeSearchClient:
    def __init__(self, hits=None, error: Exception | None = None) -> None:
        self._hits = list(hits or [])
        self._error = error
        self.queries: list[object] = []

    async def search(self, query):
        self.queries.append(query)
        if self._error is not None:
            raise self._error
        return list(self._hits)


class FakeUserPreferencesClient:
    def __init__(
        self,
        preferences: dict[UserId, UserPreferences] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._preferences = preferences or {}
        self._error = error
        self.requested: list[UserId] = []

    async def get_preferences(self, user_id: UserId) -> UserPreferences | None:
        self.requested.append(user_id)
        if self._error is not None:
            raise self._error
        return self._preferences.get(user_id)


class FakeProfileServiceClient:
    def __init__(
        self,
        profiles: dict[UserId, list[ResumeProfile]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._profiles = profiles or {}
        self._error = error
        self.requested: list[UserId] = []

    async def list_profiles(self, user_id: UserId) -> list[ResumeProfile]:
        self.requested.append(user_id)
        if self._error is not None:
            raise self._error
        return self._profiles.get(user_id, [])


def make_user_preferences(user_id: UserId, **overrides: object) -> UserPreferences:
    fields = {
        "id": UserPreferencesId(uuid4()),
        "user_id": user_id,
        "target_roles": ["Mechanical Engineer"],
        "target_locations": ["Remote"],
        "updated_at": now(),
    }
    fields.update(overrides)
    return UserPreferences(**fields)


def make_resume_profile(user_id: UserId, **overrides: object) -> ResumeProfile:
    fields = {
        "profile_id": ProfileId(uuid4()),
        "resume_id": ResumeId(uuid4()),
        "user_id": user_id,
        "title": "Mechanical Engineer",
        "skills": ["CAD"],
        "target_roles": ["Mechanical Design Engineer"],
        "status": ProfileStatus.ACTIVE,
    }
    fields.update(overrides)
    return ResumeProfile(**fields)


def make_job_source(user_id: UserId, **overrides: object) -> JobSource:
    fields = {
        "id": JobSourceId(uuid4()),
        "user_id": user_id,
        "name": "LinkedIn - Mechanical roles",
        "type": JobSourceType.LINKEDIN,
        "query_config": {},
        "enabled": True,
        "last_run_at": None,
    }
    fields.update(overrides)
    return JobSource(**fields)


def make_job(user_id: UserId, **overrides: object) -> Job:
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
        "discovered_at": now(),
    }
    fields.update(overrides)
    return Job(**fields)


__all__ = [
    "FakeExtractor",
    "FakeJobRepository",
    "FakeJobSourceRepository",
    "FakePageFetcher",
    "FakeProfileServiceClient",
    "FakeSearchClient",
    "FakeUserPreferencesClient",
    "RecordingPublisher",
    "make_extracted_fields",
    "make_job",
    "make_job_source",
    "make_resume_profile",
    "make_user_preferences",
    "now",
]
