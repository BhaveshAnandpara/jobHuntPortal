"""Session-scope accessor shared by every DB access point Contact Discovery
Service owns: `contacts.repository.ContactRepository`,
`contacts.repository.ContactScoreRepository`, and the idempotency check in
`contacts.consumers`.

Mirrors `matching.db`'s DI seam exactly (see that module's docstring for
the full rationale) — tests override via `set_session_factory(...)` to bind
every DB call in this component to one isolated SQLite in-memory engine.
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
async def contacts_session_scope() -> AsyncIterator[AsyncSession]:
    # Imported lazily so importing this module never requires a configured
    # database — the session factory is only resolved on first use, same
    # pattern as every other component's api/dependencies.py.
    from infrastructure.database import session_scope

    async with session_scope(_session_factory) as session:
        yield session


__all__ = ["contacts_session_scope", "set_session_factory"]
