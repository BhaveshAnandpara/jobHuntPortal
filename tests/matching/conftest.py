"""Shared fixtures and fakes for Job Matching Service tests.

Everything here runs against fakes or SQLite in-memory — no PostgreSQL,
Kafka broker, or LLM server is required to run `tests/matching`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import matching.consumers as consumers_module
import matching.db as db_module
import matching.events as events_module
import workflows.langgraph.job_matching.nodes as nodes_module
from shared.types.domain.user_preferences import UserPreferences
from shared.types.dto import NormalizedJob, ResumeProfile
from shared.types.enums import JobSourceType, ProfileStatus
from shared.types.ids import ProfileId, ResumeId, UserId, UserPreferencesId


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Domain factories
# ---------------------------------------------------------------------------


def make_normalized_job(user_id: UserId, **overrides: object) -> NormalizedJob:
    fields: dict[str, object] = {
        "job_id": uuid4(),
        "user_id": user_id,
        "company": "Acme Robotics",
        "title": "Senior Mechanical Design Engineer",
        "location": "Remote",
        "description": (
            "Design mechanical subsystems for industrial robots. Own CAD "
            "models from concept through DFM/DFA review, run tolerance "
            "stack-ups, and validate designs with prototype testing."
        ),
        "extracted_skills": ["CAD", "GD&T", "SolidWorks", "DFM"],
        "experience_required": "5+ years",
        "source_type": JobSourceType.MANUAL_URL,
        "source_url": "https://boards.example.com/jobs/1",
        "discovered_at": now(),
    }
    fields.update(overrides)
    return NormalizedJob(**fields)


def make_resume_profile(user_id: UserId, **overrides: object) -> ResumeProfile:
    fields: dict[str, object] = {
        "profile_id": ProfileId(uuid4()),
        "resume_id": ResumeId(uuid4()),
        "user_id": user_id,
        "title": "Mechanical Design Engineer",
        "summary": "Mechanical engineer focused on CAD and DFM.",
        "skills": ["CAD", "SolidWorks", "GD&T", "DFM"],
        "experience_years": 6.0,
        "seniority": "Senior",
        "education": [],
        "certifications": [],
        "projects": [],
        "industries": ["Robotics"],
        "target_roles": ["Mechanical Design Engineer"],
        "status": ProfileStatus.ACTIVE,
    }
    fields.update(overrides)
    return ResumeProfile(**fields)


def make_user_preferences(user_id: UserId, **overrides: object) -> UserPreferences:
    fields: dict[str, object] = {
        "id": UserPreferencesId(uuid4()),
        "user_id": user_id,
        "target_roles": ["Mechanical Design Engineer"],
        "target_locations": ["Remote"],
        "updated_at": now(),
    }
    fields.update(overrides)
    return UserPreferences(**fields)


# ---------------------------------------------------------------------------
# Fakes for ProfileServiceClient / UserPreferencesClient (matching.clients)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Fake LLMClient (matching.nodes/scoring only ever call .complete_structured)
# ---------------------------------------------------------------------------


class FakeLLMClient:
    """`LLMClient`-shaped fake. `results` maps a substring that must appear
    in the rendered prompt to either the Pydantic instance to return or an
    exception to raise — mirrors `tests/jobs/conftest.py`'s `FakeExtractor`
    substring-matching pattern, since
    `workflows.langgraph.job_matching.scoring.build_scoring_prompt` embeds
    each profile's `title` directly into the prompt text, so keying results
    by a profile's (unique, per test) `title` routes the right result to
    the right profile.
    """

    def __init__(
        self,
        results: dict[str, object] | None = None,
        default: object | None = None,
    ) -> None:
        self._results = results or {}
        self._default = default
        self.prompts: list[str] = []

    def complete_structured(self, prompt, schema, *, system=None, options=None):
        self.prompts.append(prompt)
        for key, result in self._results.items():
            if key in prompt:
                if isinstance(result, Exception):
                    raise result
                return result
        if self._default is not None:
            if isinstance(self._default, Exception):
                raise self._default
            return self._default
        raise AssertionError(
            f"FakeLLMClient has no scripted result for prompt: {prompt[:200]!r}"
        )


# ---------------------------------------------------------------------------
# Real (SQLite in-memory) async session
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """An isolated in-memory SQLite DB with `job_matches` (this component's
    own table) plus a minimal, self-contained stand-in `jobs` table (id,
    company, processing_status) so `JobProcessingStatusRepository`'s narrow
    `UPDATE processing_status` grant can be tested realistically —
    including asserting that *no other column* is touched. Deliberately
    defined locally rather than importing `jobs.models.JobRecord` (another
    component's module) so this test tree stays fully self-contained.
    """
    pytest.importorskip(
        "aiosqlite", reason="async sqlite driver not installed; DB tests need it"
    )
    from sqlalchemy import Column, MetaData, String, Table, Uuid
    from sqlalchemy.ext.asyncio import create_async_engine

    try:
        from infrastructure.database import Base
    except ImportError as exc:  # infrastructure.database's internals raise ImportError
        pytest.skip(f"infrastructure.database.Base not available yet: {exc}")

    from matching.models import JobMatchRecord

    stand_in_metadata = MetaData()
    Table(
        "jobs",
        stand_in_metadata,
        Column("id", Uuid, primary_key=True),
        Column("company", String, nullable=False),
        Column("processing_status", String, nullable=False),
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        # Scoped to exactly this component's own table rather than the full
        # `Base.metadata.create_all()` — `Base.metadata` is one process-wide
        # registry, so if another component's test module already imported
        # `jobs.models` earlier in the same pytest run (registering the
        # *real* `jobs` table, with more NOT NULL columns than this
        # fixture's minimal stand-in), a blanket create_all would create
        # that real table first and then silently skip the stand-in
        # (`checkfirst=True` sees "jobs" already exists), breaking this
        # fixture's INSERTs. Creating only `JobMatchRecord.__table__`
        # avoids the collision regardless of what else has been imported
        # into `Base.metadata` by the time this fixture runs.
        await connection.run_sync(Base.metadata.create_all, tables=[JobMatchRecord.__table__])
        await connection.run_sync(stand_in_metadata.create_all)

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
def _reset_matching_singletons() -> Iterator[None]:
    yield
    nodes_module.set_profile_service_client(None)
    nodes_module.set_llm_client(None)
    events_module.set_event_producer(None)
    consumers_module.set_graph(None)
    consumers_module.set_user_preferences_client(None)
    db_module.set_session_factory(None)


__all__ = [
    "FakeLLMClient",
    "FakeProfileServiceClient",
    "FakeUserPreferencesClient",
    "make_normalized_job",
    "make_resume_profile",
    "make_user_preferences",
    "now",
]
