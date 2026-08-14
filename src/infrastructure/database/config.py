"""Database connection and pooling configuration, read from the environment.

Defaults match `.env.example` so a local-first developer needs no
configuration at all beyond a running PostgreSQL (see
docs/architecture/overview.md#deployment-model).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_DRIVER = "postgresql+psycopg"
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 5432
DEFAULT_DATABASE = "career_platform"
DEFAULT_USER = "postgres"
DEFAULT_PASSWORD = "postgres"


@dataclass(frozen=True)
class DatabaseSettings:
    """Resolved connection settings for the one PostgreSQL database."""

    url: str
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30
    pool_recycle: int = 1800
    pool_pre_ping: bool = True
    echo: bool = False

    @property
    def is_sqlite(self) -> bool:
        return self.url.startswith("sqlite")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def build_database_url() -> str:
    """Resolve the connection URL.

    `DATABASE_URL` wins when set (it is the form `.env.example` documents);
    otherwise the URL is assembled from the discrete `POSTGRES_*` variables.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    host = os.environ.get("POSTGRES_HOST", DEFAULT_HOST)
    port = _env_int("POSTGRES_PORT", DEFAULT_PORT)
    database = os.environ.get("POSTGRES_DB", DEFAULT_DATABASE)
    user = os.environ.get("POSTGRES_USER", DEFAULT_USER)
    password = os.environ.get("POSTGRES_PASSWORD", DEFAULT_PASSWORD)
    return f"{DEFAULT_DRIVER}://{user}:{password}@{host}:{port}/{database}"


def get_database_settings() -> DatabaseSettings:
    """Read the current process environment into a `DatabaseSettings`."""
    return DatabaseSettings(
        url=build_database_url(),
        pool_size=_env_int("DATABASE_POOL_SIZE", 5),
        max_overflow=_env_int("DATABASE_MAX_OVERFLOW", 10),
        pool_timeout=_env_int("DATABASE_POOL_TIMEOUT", 30),
        pool_recycle=_env_int("DATABASE_POOL_RECYCLE", 1800),
        pool_pre_ping=_env_bool("DATABASE_POOL_PRE_PING", True),
        echo=_env_bool("DATABASE_ECHO", False),
    )


__all__ = ["DatabaseSettings", "build_database_url", "get_database_settings"]
