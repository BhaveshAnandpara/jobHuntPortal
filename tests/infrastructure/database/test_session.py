"""Session lifecycle tests.

`aiosqlite` is not among the installed dependencies (see pyproject.toml —
only `psycopg[binary]` is listed for PostgreSQL), so there is no real async
SQLite driver available in this environment. Per the task brief, the
commit/rollback/close *sequencing* of `get_session`/`session_scope` is
verified against a mocked `AsyncSession` instead of a live connection; the
engine→Base chain itself is covered separately by
`test_smoke_end_to_end.py` using the sync engine.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import infrastructure.database.session as session_module
from infrastructure.database.session import (
    create_session_factory,
    get_session,
    get_session_factory,
    reset_session_factory,
    session_scope,
)


class _FakeAsyncSessionFactory:
    """Stand-in for `async_sessionmaker`: returns pre-scripted mock sessions."""

    def __init__(self) -> None:
        self.sessions: list[AsyncMock] = []

    def __call__(self) -> AsyncMock:
        session = AsyncMock(spec=AsyncSession)
        self.sessions.append(session)
        return session


@pytest.fixture(autouse=True)
def _reset_factory_cache() -> None:
    reset_session_factory()
    yield
    reset_session_factory()


def test_create_session_factory_binds_to_given_engine() -> None:
    from infrastructure.database.config import DatabaseSettings
    from infrastructure.database.engine import create_async_database_engine

    engine = create_async_database_engine(
        DatabaseSettings(url="postgresql+psycopg://user:pass@localhost/db")
    )
    factory = create_session_factory(engine)
    assert factory.kw["bind"] is engine
    assert factory.kw["expire_on_commit"] is False


def test_get_session_factory_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost/db")
    first = get_session_factory()
    second = get_session_factory()
    assert first is second


def test_reset_session_factory_forces_a_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost/db")
    first = get_session_factory()
    reset_session_factory()
    second = get_session_factory()
    assert first is not second


@pytest.mark.asyncio
async def test_get_session_commits_on_clean_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_factory = _FakeAsyncSessionFactory()
    monkeypatch.setattr(session_module, "get_session_factory", lambda: fake_factory)

    gen = get_session()
    session = await gen.__anext__()
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()  # drives the generator past `yield` to completion

    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_session_rolls_back_and_closes_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_factory = _FakeAsyncSessionFactory()
    monkeypatch.setattr(session_module, "get_session_factory", lambda: fake_factory)

    gen = get_session()
    session = await gen.__anext__()

    with pytest.raises(ValueError):
        await gen.athrow(ValueError("boom"))

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_session_used_via_async_for_matches_fastapi_dependency_pattern() -> None:
    # Mirrors src/users/api/dependencies.py's exact usage:
    #     async for session in infrastructure_session():
    #         yield session
    #         await session.commit()
    fake_factory = _FakeAsyncSessionFactory()
    seen: list[AsyncMock] = []

    async def fake_get_session():
        session = fake_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async for session in fake_get_session():
        seen.append(session)

    assert len(seen) == 1
    seen[0].commit.assert_awaited()
    seen[0].close.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_scope_commits_on_clean_exit() -> None:
    fake_factory = _FakeAsyncSessionFactory()

    async with session_scope(factory=fake_factory) as session:
        pass

    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_scope_rolls_back_on_exception() -> None:
    fake_factory = _FakeAsyncSessionFactory()

    with pytest.raises(RuntimeError):
        async with session_scope(factory=fake_factory):
            raise RuntimeError("boom")

    used_session = fake_factory.sessions[0]
    used_session.rollback.assert_awaited_once()
    used_session.commit.assert_not_awaited()
    used_session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_scope_uses_process_wide_factory_when_none_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_factory = _FakeAsyncSessionFactory()
    monkeypatch.setattr(session_module, "get_session_factory", lambda: fake_factory)

    async with session_scope() as session:
        pass

    session.commit.assert_awaited_once()
