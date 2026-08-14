"""FastAPI dependencies for the Resume/Profile Service router."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.kafka.producer import EventProducer
from infrastructure.llm import LLMClient
from profiles.repository import CandidateProfileRepository, ResumeRepository
from profiles.service import ProfileService
from profiles.storage import LocalResumeStorage

_producer: EventProducer | None = None
_storage: LocalResumeStorage | None = None
_llm_client: LLMClient | None = None


async def get_session() -> AsyncIterator[AsyncSession]:
    # Imported lazily so mounting this router in api/main.py never requires a
    # configured database — the session factory is only resolved per request.
    from infrastructure.database import get_session as infrastructure_session

    async for session in infrastructure_session():
        yield session
        # Reached only when the handler returned without raising, so a failed
        # request leaves nothing written. Note: FastAPI runs a yield
        # dependency's post-yield code *after* any BackgroundTasks scheduled
        # during the request (verified against this project's FastAPI
        # version), so `POST /resumes` scheduling
        # `service.run_parsing_workflow` as a background task still runs on
        # this same, still-open session/transaction — commit here covers
        # both the upload and the parsing result together.
        await session.commit()


def get_event_producer() -> EventProducer:
    """Process-wide `EventProducer`, lazily constructed on first use so
    importing this module never requires a reachable Kafka broker.
    """
    global _producer
    if _producer is None:
        from profiles.events import PRODUCER_NAME

        _producer = EventProducer(PRODUCER_NAME)
    return _producer


def get_resume_storage() -> LocalResumeStorage:
    global _storage
    if _storage is None:
        _storage = LocalResumeStorage()
    return _storage


def get_llm_client() -> LLMClient:
    """Process-wide `LLMClient`, lazily constructed on first use.

    `infrastructure/llm/` now exports a concrete `LLMClient` (the LLM
    Provider Layer's single public entry point — see
    docs/architecture/service-boundaries.md#llm-provider-layer): with no
    provider injected, it defaults to `OllamaProvider` built from
    `LLMConfig.from_env()`, matching about_project.md's local-first Ollama
    default. Constructing it here performs no network call — `OllamaProvider`
    only opens a connection when `.complete()` is actually invoked (inside
    `ProfileService.run_parsing_workflow`) — so importing/mounting this
    router never requires a reachable Ollama server. Tests inject a fake
    `LLMProvider` into their own `LLMClient(provider=...)` directly via
    `ProfileService(...)` instead of going through this dependency.
    """
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


def get_profile_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProfileService:
    return ProfileService(
        ResumeRepository(session),
        CandidateProfileRepository(session),
        producer=get_event_producer(),
        storage=get_resume_storage(),
        llm_client=get_llm_client(),
    )


__all__ = [
    "get_event_producer",
    "get_llm_client",
    "get_profile_service",
    "get_resume_storage",
    "get_session",
]
