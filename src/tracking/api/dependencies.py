"""FastAPI dependencies for the Tracking Service router."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from tracking.repository import ApplicationHistoryRepository, ApplicationRepository


async def get_session() -> AsyncIterator[AsyncSession]:
    # Imported lazily so mounting this router in api/main.py never requires
    # a configured database — the session factory is only resolved per
    # request, same pattern as every other component's api/dependencies.py.
    from infrastructure.database import get_session as infrastructure_session

    async for session in infrastructure_session():
        yield session
        await session.commit()


def get_application_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApplicationRepository:
    return ApplicationRepository(session)


def get_application_history_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApplicationHistoryRepository:
    return ApplicationHistoryRepository(session)


__all__ = [
    "get_application_history_repository",
    "get_application_repository",
    "get_session",
]
