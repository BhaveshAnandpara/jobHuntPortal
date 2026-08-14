"""Shared fixtures and fakes for Contact Discovery Service tests.

Everything here runs against fakes or SQLite in-memory — no PostgreSQL,
Kafka broker, or LLM/people-search server is required to run
`tests/contacts` or `tests/workflows/langgraph/contact_discovery`. Mirrors
`tests/matching/conftest.py`'s established pattern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import contacts.consumers as consumers_module
import contacts.db as db_module
import contacts.events as events_module
import workflows.langgraph.contact_discovery.nodes as nodes_module
from infrastructure.external.people_search import PersonSearchHit
from shared.types.ids import JobId, UserId


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Domain/event factories
# ---------------------------------------------------------------------------


def make_search_request(**overrides: object):
    from shared.events.payloads import ContactSearchRequest

    fields: dict[str, object] = {
        "job_id": JobId(uuid4()),
        "user_id": UserId(uuid4()),
        "company": "Acme Robotics",
        "title": "Senior Mechanical Design Engineer",
        "location": "Remote",
    }
    fields.update(overrides)
    return ContactSearchRequest(**fields)


def make_hit(**overrides: object) -> PersonSearchHit:
    fields: dict[str, object] = {
        "full_name": "Jordan Smith",
        "provider": "static",
        "headline": "Mechanical Design Engineer at Acme Robotics",
        "company": "Acme Robotics",
        "profile_url": "https://example.com/in/jordan-smith",
        "email": None,
    }
    fields.update(overrides)
    return PersonSearchHit(**fields)


# ---------------------------------------------------------------------------
# Fakes for PeopleSearchClient / LLMClient
# ---------------------------------------------------------------------------


class FakePeopleSearchClient:
    """Duck-typed stand-in for `infrastructure.external.people_search
    .PeopleSearchClient` — exposes only the `async search(query)` method
    the nodes call, matching `FakeProfileServiceClient`'s style in
    `tests/matching/conftest.py`.
    """

    def __init__(
        self,
        hits: list[PersonSearchHit] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._hits = hits or []
        self._error = error
        self.queries: list[object] = []

    async def search(self, query):
        self.queries.append(query)
        if self._error is not None:
            raise self._error
        return list(self._hits)


class FakeLLMClient:
    """`LLMClient`-shaped fake. `results` maps a substring that must appear
    in the rendered prompt to either the Pydantic instance to return or an
    exception to raise — same pattern as `tests/matching/conftest.py`'s
    `FakeLLMClient`.
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
    """An isolated in-memory SQLite DB with this component's own tables
    (`contacts`, `contact_rankings`) — mirrors
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

    from contacts.models import ContactRecord, ContactScoreRecord

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            tables=[ContactRecord.__table__, ContactScoreRecord.__table__],
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
def _reset_contacts_singletons() -> Iterator[None]:
    yield
    nodes_module.set_people_search_client(None)
    nodes_module.set_llm_client(None)
    events_module.set_event_producer(None)
    consumers_module.set_graph(None)
    db_module.set_session_factory(None)


__all__ = [
    "FakeLLMClient",
    "FakePeopleSearchClient",
    "make_hit",
    "make_search_request",
    "now",
]
