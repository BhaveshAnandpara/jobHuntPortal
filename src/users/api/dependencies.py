"""FastAPI dependencies for the User Service router."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from users.repository import UserPreferencesRepository, UserRepository
from users.service import UserService


async def get_session() -> AsyncIterator[AsyncSession]:
    # Imported lazily so mounting this router in api/main.py never requires a
    # configured database — the session factory is only resolved per request.
    from infrastructure.database import get_session as infrastructure_session

    async for session in infrastructure_session():
        yield session
        # Reached only when the handler returned without raising, so a failed
        # request leaves nothing written.
        await session.commit()


def get_user_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserService:
    return UserService(
        users=UserRepository(session),
        preferences=UserPreferencesRepository(session),
    )


__all__ = ["get_session", "get_user_service"]
