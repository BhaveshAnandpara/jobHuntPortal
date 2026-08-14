"""Shared fixtures and fakes for Outreach Service tests.

Everything here runs against fakes or SQLite in-memory — no PostgreSQL,
Kafka broker, LLM server, or real send provider is required to run
`tests/outreach` or `tests/workflows/langgraph/outreach_generation`.
Mirrors `tests/matching/conftest.py`'s / `tests/contacts/conftest.py`'s
established pattern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import outreach.consumers as consumers_module
import outreach.db as db_module
import outreach.events as events_module
import workflows.langgraph.outreach_generation.nodes as nodes_module
from shared.types.api.jobs import JobResponse
from shared.types.api.matching import JobMatchResponse
from shared.types.dto import RankedContact, ResumeProfile
from shared.types.enums import (
    ContactType,
    JobProcessingStatus,
    MatchRecommendation,
    ProfileStatus,
)
from shared.types.ids import ContactId, JobId, ProfileId, ResumeId, UserId


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Domain/event factories
# ---------------------------------------------------------------------------


def make_ranked_contact(**overrides: object) -> RankedContact:
    fields: dict[str, object] = {
        "contact_id": ContactId(uuid4()),
        "full_name": "Jordan Smith",
        "headline": "Senior Mechanical Engineer at Acme Robotics",
        "contact_type": ContactType.PRACTITIONER,
        "profile_url": "https://example.com/in/jordan-smith",
        "relevance_score": 8.5,
    }
    fields.update(overrides)
    return RankedContact(**fields)


def make_resume_profile(user_id: UserId | None = None, **overrides: object) -> ResumeProfile:
    fields: dict[str, object] = {
        "profile_id": ProfileId(uuid4()),
        "resume_id": ResumeId(uuid4()),
        "user_id": user_id or UserId(uuid4()),
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


def make_job_match_response(**overrides: object) -> JobMatchResponse:
    fields: dict[str, object] = {
        "job_match_id": uuid4(),
        "selected_profile_id": ProfileId(uuid4()),
        "selected_resume_id": ResumeId(uuid4()),
        "match_score": 0.91,
        "matched_skills": ["CAD"],
        "missing_skills": [],
        "recommendation": MatchRecommendation.SHORTLIST,
        "matched_at": now(),
    }
    fields.update(overrides)
    return JobMatchResponse(**fields)


def make_job_response(job_id: JobId | None = None, **overrides: object) -> JobResponse:
    fields: dict[str, object] = {
        "id": job_id or JobId(uuid4()),
        "user_id": UserId(uuid4()),
        "company": "Acme Robotics",
        "title": "Senior Mechanical Design Engineer",
        "location": "Remote",
        "description": "Own CAD models from concept through DFM/DFA review.",
        "extracted_skills": ["CAD", "SolidWorks"],
        "experience_required": "5+ years",
        "source_url": "https://boards.example.com/jobs/acme-robotics-1",
        "processing_status": JobProcessingStatus.MATCHED,
        "discovered_at": now(),
    }
    fields.update(overrides)
    return JobResponse(**fields)


# ---------------------------------------------------------------------------
# Fakes for JobMatchClient / ProfileServiceClient (outreach.clients)
# ---------------------------------------------------------------------------


class FakeJobMatchClient:
    def __init__(
        self,
        matches: dict[JobId, JobMatchResponse] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._matches = matches or {}
        self._error = error
        self.requested: list[JobId] = []

    async def get_match(self, job_id: JobId) -> JobMatchResponse | None:
        self.requested.append(job_id)
        if self._error is not None:
            raise self._error
        return self._matches.get(job_id)


class FakeProfileServiceClient:
    def __init__(
        self,
        profiles: dict[ProfileId, ResumeProfile] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._profiles = profiles or {}
        self._error = error
        self.requested: list[ProfileId] = []

    async def get_profile(self, profile_id: ProfileId) -> ResumeProfile | None:
        self.requested.append(profile_id)
        if self._error is not None:
            raise self._error
        return self._profiles.get(profile_id)


class FakeJobIngestionClient:
    def __init__(
        self,
        jobs: dict[JobId, JobResponse] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._jobs = jobs or {}
        self._error = error
        self.requested: list[JobId] = []

    async def get_job(self, job_id: JobId) -> JobResponse | None:
        self.requested.append(job_id)
        if self._error is not None:
            raise self._error
        return self._jobs.get(job_id)


# ---------------------------------------------------------------------------
# Fake LLMClient (nodes/generation only ever call .complete_structured)
# ---------------------------------------------------------------------------


class FakeLLMClient:
    """`LLMClient`-shaped fake. `results` maps a substring that must appear
    in the rendered prompt to either the Pydantic instance to return or an
    exception to raise — same pattern as `tests/matching/conftest.py`'s /
    `tests/contacts/conftest.py`'s `FakeLLMClient`.
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
    """An isolated in-memory SQLite DB with this component's own table
    (`outreach`) — mirrors `tests/matching/conftest.py`'s /
    `tests/contacts/conftest.py`'s `session_factory` fixture.
    """
    pytest.importorskip(
        "aiosqlite", reason="async sqlite driver not installed; DB tests need it"
    )
    from sqlalchemy.ext.asyncio import create_async_engine

    try:
        from infrastructure.database import Base
    except ImportError as exc:  # infrastructure.database's internals raise ImportError
        pytest.skip(f"infrastructure.database.Base not available yet: {exc}")

    from outreach.models import OutreachRecord

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all, tables=[OutreachRecord.__table__]
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
def _reset_outreach_singletons() -> Iterator[None]:
    yield
    nodes_module.set_llm_client(None)
    events_module.set_event_producer(None)
    consumers_module.set_graph(None)
    consumers_module.set_job_match_client(None)
    consumers_module.set_profile_service_client(None)
    consumers_module.set_job_ingestion_client(None)
    consumers_module.set_message_send_client(None)
    db_module.set_session_factory(None)


__all__ = [
    "FakeJobIngestionClient",
    "FakeJobMatchClient",
    "FakeLLMClient",
    "FakeProfileServiceClient",
    "make_job_match_response",
    "make_job_response",
    "make_ranked_contact",
    "make_resume_profile",
    "now",
]
