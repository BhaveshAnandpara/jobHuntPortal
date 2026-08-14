"""Tests for connection/pooling configuration resolution from the environment."""

from __future__ import annotations

import pytest

from infrastructure.database.config import (
    DatabaseSettings,
    build_database_url,
    get_database_settings,
)

ENV_VARS = (
    "DATABASE_URL",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "DATABASE_POOL_SIZE",
    "DATABASE_MAX_OVERFLOW",
    "DATABASE_POOL_TIMEOUT",
    "DATABASE_POOL_RECYCLE",
    "DATABASE_POOL_PRE_PING",
    "DATABASE_ECHO",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_build_database_url_defaults_assemble_postgres_psycopg_url() -> None:
    url = build_database_url()
    assert url == "postgresql+psycopg://postgres:postgres@localhost:5432/career_platform"


def test_database_url_env_var_wins_over_discrete_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("POSTGRES_HOST", "should-be-ignored")
    assert build_database_url() == "sqlite+aiosqlite:///:memory:"


def test_discrete_vars_assemble_url_when_database_url_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_DB", "custom_db")
    monkeypatch.setenv("POSTGRES_USER", "svc")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    assert build_database_url() == "postgresql+psycopg://svc:secret@db.internal:6543/custom_db"


def test_get_database_settings_reads_pool_options_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_POOL_SIZE", "20")
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "40")
    monkeypatch.setenv("DATABASE_POOL_TIMEOUT", "5")
    monkeypatch.setenv("DATABASE_POOL_RECYCLE", "60")
    monkeypatch.setenv("DATABASE_POOL_PRE_PING", "false")
    monkeypatch.setenv("DATABASE_ECHO", "true")

    settings = get_database_settings()

    assert settings.pool_size == 20
    assert settings.max_overflow == 40
    assert settings.pool_timeout == 5
    assert settings.pool_recycle == 60
    assert settings.pool_pre_ping is False
    assert settings.echo is True


def test_get_database_settings_defaults_match_documented_pool_shape() -> None:
    settings = get_database_settings()
    assert settings == DatabaseSettings(url=build_database_url())


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("sqlite:///:memory:", True),
        ("sqlite+aiosqlite:///:memory:", True),
        ("postgresql+psycopg://u:p@h/db", False),
    ],
)
def test_is_sqlite_detects_sqlite_urls(url: str, expected: bool) -> None:
    assert DatabaseSettings(url=url).is_sqlite is expected


def test_database_settings_is_frozen() -> None:
    settings = DatabaseSettings(url="sqlite:///:memory:")
    with pytest.raises(AttributeError):
        settings.url = "sqlite:///other.db"  # type: ignore[misc]
