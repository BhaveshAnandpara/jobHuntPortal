"""Session-scope accessor for every DB read Resume Tailoring Service makes.

Defaults to `infrastructure.database`'s process-wide `session_scope`; tests
override via `set_session_factory(...)` to bind this component's DB access
to one isolated SQLite in-memory engine for the duration of a test.

Mirrors `matching.db` / `contacts.db` / `outreach.db` / `infrastructure.llm
.user_client` exactly — a single, deliberate DI seam so tests configure the
database in one place rather than once per module.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_session_factory: async_sessionmaker[AsyncSession] | None = None


def set_session_factory(factory: async_sessionmaker[AsyncSession] | None) -> None:
    """Test seam. Pass `None` to restore the process-wide default."""
    global _session_factory
    _session_factory = factory


@asynccontextmanager
async def tailoring_session_scope() -> AsyncIterator[AsyncSession]:
    # Imported lazily so importing this module never requires a configured
    # database — the session factory is only resolved on first use, same
    # pattern as every other component's db.py/api/dependencies.py.
    from infrastructure.database import session_scope

    async with session_scope(_session_factory) as session:
        yield session


__all__ = ["set_session_factory", "tailoring_session_scope"]
