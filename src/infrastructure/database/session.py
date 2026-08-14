"""Session lifecycle used by every component's `repository.py`.

Every component's `repository.py` constructor already types its session
parameter as `sqlalchemy.ext.asyncio.AsyncSession` (see `users/repository.py`,
`profiles/repository.py`), and `users/api/dependencies.py` already consumes
this module's `get_session` as an async generator:

    from infrastructure.database import get_session as infrastructure_session

    async for session in infrastructure_session():
        yield session
        await session.commit()

So `get_session` must stay an `async def ... yield` generator (not an
`@asynccontextmanager`), because only a plain async-generator function is
usable with `async for`. Semantics: commits on clean exit, rolls back on any
exception, always closes — so no component reimplements transaction
handling.

`session_scope` offers the same commit/rollback/close semantics via
`async with`, for callers outside a request-scoped dependency chain (tests,
scripts, workers).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from infrastructure.database.engine import get_async_engine

_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build an `async_sessionmaker` bound to an explicit engine, without caching it."""
    return async_sessionmaker(bind=engine, expire_on_commit=False)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory, bound to the shared async engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = create_session_factory(get_async_engine())
    return _session_factory


def reset_session_factory() -> None:
    """Drop the cached session factory so the next call rebinds to a new engine."""
    global _session_factory
    _session_factory = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a transactional `AsyncSession`: commit on success, rollback on error.

    Async-generator form so it is directly usable with `async for` (the
    FastAPI-dependency pattern already in `users/api/dependencies.py`) as
    well as being driveable manually via `.__anext__()`/`asend()` in tests.
    """
    session = get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """Same transactional semantics as `get_session`, as an `async with` context manager.

    Used by tests and by any caller that must bind to an engine other than
    the process-wide one (pass an explicit `factory`), or that is not
    itself inside an `async for` dependency chain.
    """
    factory = factory if factory is not None else get_session_factory()
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


__all__ = [
    "create_session_factory",
    "get_session",
    "get_session_factory",
    "reset_session_factory",
    "session_scope",
]
