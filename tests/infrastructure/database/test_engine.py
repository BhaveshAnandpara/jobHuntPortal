"""Engine construction/lifecycle tests.

Sync-engine tests run against real SQLite in-memory (no extra driver
needed). Async-engine tests only exercise *construction* against a
`postgresql+psycopg` URL (psycopg is an installed dependency, and building
an `AsyncEngine` resolves the dialect without opening a network
connection) — no live PostgreSQL is required or contacted.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

import infrastructure.database.engine as engine_module
from infrastructure.database.config import DatabaseSettings
from infrastructure.database.engine import (
    create_async_database_engine,
    create_database_engine,
    dispose_async_engine,
    dispose_engine,
    get_async_engine,
    get_engine,
)


@pytest.fixture(autouse=True)
def _reset_cached_engines() -> None:
    # get_engine()/get_async_engine() cache a process-wide singleton; each
    # test must start from a clean slate so env/settings changes take effect.
    dispose_engine()
    yield
    dispose_engine()


def test_create_database_engine_builds_sync_engine_for_sqlite() -> None:
    settings = DatabaseSettings(url="sqlite:///:memory:")
    engine = create_database_engine(settings)
    try:
        assert isinstance(engine, Engine)
        assert str(engine.url) == "sqlite:///:memory:"
    finally:
        engine.dispose()


def test_create_database_engine_omits_pool_sizing_for_sqlite() -> None:
    # QueuePool kwargs (pool_size/max_overflow/...) are rejected by SQLite's
    # own pool implementation; engine.py must not pass them through.
    settings = DatabaseSettings(url="sqlite:///:memory:")
    engine = create_database_engine(settings)  # would raise TypeError if misconfigured
    engine.dispose()


def test_create_database_engine_applies_pool_settings_for_non_sqlite() -> None:
    settings = DatabaseSettings(
        url="postgresql+psycopg://user:pass@localhost/db",
        pool_size=7,
        max_overflow=3,
        pool_timeout=11,
        pool_recycle=99,
        pool_pre_ping=False,
    )
    engine = create_database_engine(settings)
    try:
        assert engine.pool.size() == 7
    finally:
        engine.dispose()


def test_get_engine_returns_cached_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    first = get_engine()
    second = get_engine()
    assert first is second


def test_dispose_engine_clears_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    first = get_engine()
    dispose_engine()
    second = get_engine()
    assert first is not second


def test_create_async_database_engine_builds_async_engine() -> None:
    settings = DatabaseSettings(url="postgresql+psycopg://user:pass@localhost/db")
    engine = create_async_database_engine(settings)
    assert isinstance(engine, AsyncEngine)
    # str(url) masks the password by default; render_as_string(hide_password=False)
    # is the documented way to get the literal URL back for comparison.
    assert engine.url.render_as_string(hide_password=False) == (
        "postgresql+psycopg://user:pass@localhost/db"
    )


def test_create_async_database_engine_applies_pool_settings_for_non_sqlite() -> None:
    settings = DatabaseSettings(
        url="postgresql+psycopg://user:pass@localhost/db",
        pool_size=4,
        max_overflow=2,
    )
    engine = create_async_database_engine(settings)
    assert engine.pool.size() == 4


@pytest.mark.asyncio
async def test_get_async_engine_returns_cached_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost/db")
    engine_module._async_engine = None
    try:
        first = get_async_engine()
        second = get_async_engine()
        assert first is second
    finally:
        await dispose_async_engine()


@pytest.mark.asyncio
async def test_dispose_async_engine_clears_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost/db")
    engine_module._async_engine = None
    first = get_async_engine()
    await dispose_async_engine()
    second = get_async_engine()
    try:
        assert first is not second
    finally:
        await dispose_async_engine()


def test_sqlite_memory_engine_persists_state_across_connections_within_a_test() -> None:
    # engine.py must not force a pool implementation that drops in-memory
    # SQLite state between connections — that would break every SQLite-backed
    # test in this package, including the create_all/insert/select smoke test.
    settings = DatabaseSettings(url="sqlite:///:memory:")
    engine = create_database_engine(settings)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("CREATE TABLE t (id INTEGER)")
        with engine.connect() as connection:
            result = connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='t'"
            )
            assert result.fetchone() is not None
    finally:
        engine.dispose()
