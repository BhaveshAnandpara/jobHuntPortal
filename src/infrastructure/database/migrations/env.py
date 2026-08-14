"""Alembic environment.

Wired to the shared `Base.metadata` so that once a component defines its
tables in its own `models.py`, `alembic revision --autogenerate` picks them
up. The *content* of each migration is authored by the component that owns
the table (docs/architecture/database-ownership.md); this file only makes
the tooling work.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from logging.config import fileConfig
from pathlib import Path

from alembic import context

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from infrastructure.database.config import (
    DatabaseSettings,
    get_database_settings,
)
from infrastructure.database.engine import create_database_engine
from infrastructure.database.metadata import load_metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = load_metadata()


def _settings() -> DatabaseSettings:
    # `-x url=...` overrides the environment, which is how a caller points a
    # migration run at a throwaway database.
    settings = get_database_settings()
    override = context.get_x_argument(as_dictionary=True).get("url")
    return replace(settings, url=override) if override else settings


def run_migrations_offline() -> None:
    context.configure(
        url=_settings().url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_database_engine(_settings())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
