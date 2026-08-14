"""Database connectivity check for startup probes and health endpoints.

Both a sync (`check_connection`, using the sync engine reserved for
Alembic) and async (`check_connection_async`, using the app-runtime async
engine) variant are provided, mirroring the two engine flavors in
`engine.py`. Both normalize every `SQLAlchemyError` into `False` plus a
logged exception rather than letting a connectivity failure raise out of a
health probe.
"""

from __future__ import annotations

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from infrastructure.database.engine import get_async_engine, get_engine
from infrastructure.logging import get_logger

logger = get_logger(__name__)


def check_connection(engine: Engine | None = None) -> bool:
    """Return whether a `SELECT 1` succeeds against the database (sync engine)."""
    target = engine if engine is not None else get_engine()
    try:
        with target.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Database connectivity check failed")
        return False
    return True


async def check_connection_async(engine: AsyncEngine | None = None) -> bool:
    """Return whether a `SELECT 1` succeeds against the database (async engine)."""
    target = engine if engine is not None else get_async_engine()
    try:
        async with target.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Database connectivity check failed")
        return False
    return True


__all__ = ["check_connection", "check_connection_async"]
