"""One-off local setup helper: create every component's tables against a
real database, since no Alembic migrations exist yet (it's declared as a
dependency in pyproject.toml but never wired up — the test suite creates
tables the same way, via `Base.metadata.create_all`, against SQLite; this
script does the identical thing against whatever `DATABASE_URL`/`POSTGRES_*`
env vars resolve to, per infrastructure/database/config.py).

Not part of the application itself — never imported by src/ or tests/.
Safe to re-run: `create_all` only creates tables that don't already exist.

Usage (from the repo root, with the project importable — e.g. after
`pip install -e .`):

    python scripts/bootstrap_db.py
"""

from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

# Loads a repo-root `.env` (if present) — same reasoning as api/main.py's
# module docstring. This script never imports api.main, so it needs its
# own call.
load_dotenv()

# Windows-only: psycopg's async mode cannot run on asyncio's default
# ProactorEventLoop (see psycopg.InterfaceError raised without this). Real
# `uvicorn api.main:app` runs on Windows need the same fix — see
# scripts/run_server.py, which is what this project's real (non-Docker)
# entry point uses instead of the bare `uvicorn` CLI for that reason.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from infrastructure.database.engine import get_async_engine
from infrastructure.database.metadata import load_metadata


async def main() -> None:
    metadata = load_metadata()
    engine = get_async_engine()
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    table_names = sorted(metadata.tables.keys())
    print(f"Created/verified {len(table_names)} table(s): {', '.join(table_names)}")


if __name__ == "__main__":
    asyncio.run(main())
