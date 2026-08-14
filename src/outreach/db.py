"""Session-scope accessor shared by every DB access point Outreach Service
owns: `outreach.repository.OutreachRepository`, and the idempotency checks
in `outreach.consumers` (both the `contacts.found` consumer and the
`outreach.approved` send-worker consumer).

Mirrors `matching.db`'s / `contacts.db`'s DI seam exactly (see those
modules' docstrings for the full rationale) — tests override via
`set_session_factory(...)` to bind every DB call in this component to one
isolated SQLite in-memory engine for the duration of a test.
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
async def outreach_session_scope() -> AsyncIterator[AsyncSession]:
    # Imported lazily so importing this module never requires a configured
    # database — the session factory is only resolved on first use, same
    # pattern as every other component's api/dependencies.py.
    from infrastructure.database import session_scope

    async with session_scope(_session_factory) as session:
        yield session


__all__ = ["outreach_session_scope", "set_session_factory"]
