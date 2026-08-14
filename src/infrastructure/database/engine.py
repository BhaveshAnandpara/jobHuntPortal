"""SQLAlchemy engine construction and process-wide engine lifecycle.

Connection pooling lives here so no component duplicates pool configuration
in its own `repository.py`.

Two engine flavors are built from the same `DatabaseSettings`:

- The **sync** engine (`create_database_engine` / `get_engine`) exists only
  for Alembic's migration runner (`migrations/env.py`), which does not run
  through an asyncio engine.
- The **async** engine (`create_async_database_engine` / `get_async_engine`)
  backs the app-runtime session factory in `session.py`, which every
  component's `repository.py` depends on (see
  docs/architecture/dependency-graph.md#4-database-dependencies) via
  `AsyncSession`.

Both share the same `postgresql+psycopg` driver in production: SQLAlchemy's
psycopg (v3) dialect supports sync and async engines from the identical URL,
selected purely by which `create_*engine` factory is used.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from infrastructure.database.config import DatabaseSettings, get_database_settings
from infrastructure.logging import format_context, get_logger

logger = get_logger(__name__)

_engine: Engine | None = None
_async_engine: AsyncEngine | None = None


def _redacted_target(url: str) -> str:
    """`driver://host:port/database` with any credentials stripped — never
    log `DatabaseSettings.url` directly, since the discrete-variables path
    in `config.py` defaults to embedding a username/password in it."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}{parts.path}"


def _pool_options(settings: DatabaseSettings) -> dict[str, Any]:
    options: dict[str, Any] = {"echo": settings.echo, "future": True}

    # SQLite (used by the deterministic test suite) selects its own pool
    # implementation, which rejects the QueuePool sizing arguments.
    if not settings.is_sqlite:
        options.update(
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
            pool_timeout=settings.pool_timeout,
            pool_recycle=settings.pool_recycle,
            pool_pre_ping=settings.pool_pre_ping,
        )
    return options


def create_database_engine(settings: DatabaseSettings) -> Engine:
    """Build a new sync `Engine` from explicit settings, without caching it.

    Used by Alembic's migration environment only — see module docstring.
    """
    return create_engine(settings.url, **_pool_options(settings))


def get_engine() -> Engine:
    """Return the process-wide sync engine, creating it from the environment once."""
    global _engine
    if _engine is None:
        settings = get_database_settings()
        _engine = create_database_engine(settings)
        logger.info(
            "Database engine initialized | %s",
            format_context(kind="sync", target=_redacted_target(settings.url), pool_size=settings.pool_size),
        )
    return _engine


def dispose_engine() -> None:
    """Close all pooled connections and drop the cached sync engine."""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def create_async_database_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Build a new `AsyncEngine` from explicit settings, without caching it.

    Backs `session.py`'s app-runtime `AsyncSession` factory.
    """
    return create_async_engine(settings.url, **_pool_options(settings))


def get_async_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it from the environment once."""
    global _async_engine
    if _async_engine is None:
        settings = get_database_settings()
        _async_engine = create_async_database_engine(settings)
        logger.info(
            "Database engine initialized | %s",
            format_context(kind="async", target=_redacted_target(settings.url), pool_size=settings.pool_size),
        )
    return _async_engine


async def dispose_async_engine() -> None:
    """Close all pooled connections and drop the cached async engine."""
    global _async_engine
    if _async_engine is not None:
        await _async_engine.dispose()
        _async_engine = None


__all__ = [
    "create_async_database_engine",
    "create_database_engine",
    "dispose_async_engine",
    "dispose_engine",
    "get_async_engine",
    "get_engine",
]
