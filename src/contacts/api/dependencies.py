"""FastAPI dependencies for the Contact Discovery Service router."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from contacts.events import get_event_producer
from infrastructure.kafka.producer import EventProducer


async def get_session() -> AsyncIterator[AsyncSession]:
    # Imported lazily so mounting this router in api/main.py never requires
    # a configured database — the session factory is only resolved per
    # request, same pattern as every other component's api/dependencies.py.
    from infrastructure.database import get_session as infrastructure_session

    async for session in infrastructure_session():
        yield session
        await session.commit()


SessionDep = Annotated[AsyncSession, Depends(get_session)]
EventProducerDep = Annotated[EventProducer, Depends(get_event_producer)]

__all__ = ["EventProducerDep", "SessionDep", "get_session"]
