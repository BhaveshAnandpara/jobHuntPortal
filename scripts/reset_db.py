"""Local dev helper: wipe every table's data so the system can be exercised
manually as if it were a fresh install. Truncates in place (schema/tables
stay, per `infrastructure/database/metadata.py`'s discovery) rather than
dropping/recreating — faster and avoids re-running `bootstrap_db.py`.

Not part of the application itself — never imported by src/ or tests/.

Usage (from the repo root, with the project importable):

    python scripts/reset_db.py            # prompts for confirmation
    python scripts/reset_db.py --yes      # skips the prompt
    python scripts/reset_db.py --yes --with-files   # also clears data/resumes/
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

# Loads a repo-root `.env` (if present) — same reasoning as api/main.py's
# module docstring. This script never imports api.main, so it needs its
# own call.
load_dotenv()

# Windows-only: psycopg's async mode cannot run on asyncio's default
# ProactorEventLoop — see scripts/bootstrap_db.py's identical note.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text  # noqa: E402

from infrastructure.database.config import get_database_settings  # noqa: E402
from infrastructure.database.engine import _redacted_target, get_async_engine  # noqa: E402
from infrastructure.database.metadata import load_metadata  # noqa: E402

RESUME_STORAGE_DIR = Path("data") / "resumes"


async def truncate_all(table_names: list[str]) -> None:
    engine = get_async_engine()
    quoted = ", ".join(f'"{name}"' for name in table_names)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


def clear_resume_files() -> int:
    if not RESUME_STORAGE_DIR.exists():
        return 0
    count = sum(1 for _ in RESUME_STORAGE_DIR.rglob("*") if _.is_file())
    shutil.rmtree(RESUME_STORAGE_DIR)
    RESUME_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    return count


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument(
        "--with-files",
        action="store_true",
        help="also delete stored resume files under data/resumes/",
    )
    args = parser.parse_args()

    settings = get_database_settings()
    target = _redacted_target(settings.url)
    metadata = load_metadata()
    table_names = sorted(metadata.tables.keys())

    print(f"About to TRUNCATE {len(table_names)} table(s) on {target}:")
    for name in table_names:
        print(f"  - {name}")
    if args.with_files:
        print(f"Also deleting all files under {RESUME_STORAGE_DIR}/")

    if not args.yes:
        reply = input("Type 'reset' to confirm: ").strip()
        if reply != "reset":
            print("Aborted — no changes made.")
            return

    await truncate_all(table_names)
    print(f"Truncated {len(table_names)} table(s).")

    if args.with_files:
        removed = clear_resume_files()
        print(f"Removed {removed} file(s) under {RESUME_STORAGE_DIR}/.")


if __name__ == "__main__":
    asyncio.run(main())
