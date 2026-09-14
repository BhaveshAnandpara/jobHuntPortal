"""FastAPI dependencies for the Resume/Profile Service router."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.auth.errors import AuthError
from infrastructure.auth.jwt import decode_access_token
from infrastructure.kafka.producer import EventProducer
from infrastructure.llm import LLMClient, LlmNotConfiguredError, get_user_llm_client
from infrastructure.logging import get_logger
from profiles.repository import CandidateProfileRepository, ResumeRepository
from profiles.service import ProfileService
from profiles.storage import LocalResumeStorage
from shared.types.ids import UserId

logger = get_logger(__name__)

_producer: EventProducer | None = None
_storage: LocalResumeStorage | None = None


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


_optional_bearer_scheme = HTTPBearer(auto_error=False)


async def get_optional_user_id(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_optional_bearer_scheme)
    ],
) -> UserId | None:
    """The caller's `user_id` when a valid bearer token is present, else None.

    `get_profile_service` backs *five* routes, only two of which take
    `CurrentUserIdDependency` today (`POST /resumes`, `GET /resumes`,
    `GET /profiles`); `DELETE /resumes/{id}` and `GET /profiles/{id}` are
    currently unauthenticated, and `GET /profiles/{id}` is called
    server-to-server with no Authorization header by
    `outreach.clients.ProfileServiceClient`. Depending on the strict
    `get_current_user_id` here would therefore have silently added a 401 to
    two routes and broken outreach generation — a behavior change well
    outside "how do I get my `LLMClient`". This tolerant variant resolves an
    identity when one is available and returns None otherwise, leaving every
    route's auth behavior exactly as it was.
    """
    if credentials is None:
        return None
    try:
        return decode_access_token(credentials.credentials)
    except AuthError:
        return None


async def get_llm_client(
    user_id: Annotated[UserId | None, Depends(get_optional_user_id)],
) -> LLMClient | None:
    """The calling user's own `LLMClient`, or None if they have none.

    Resume parsing runs on the user's own provider credentials
    (docs/llm-provider-config/PRODUCT.md), so this resolves per request via
    `get_user_llm_client` instead of caching one process-wide client built
    from the operator's `LLMConfig.from_env()`.

    Returns None — rather than propagating `LlmNotConfiguredError` — when
    the user has no validated configuration, because this dependency is
    resolved for every route on this router, including the four that never
    touch the LLM (`GET /resumes`, `DELETE /resumes/{id}`, `GET /profiles`,
    `GET /profiles/{id}`); raising here would break all of them for a user
    who has simply not set a key yet. None is an already-supported value
    with defined behavior: `ProfileService.run_parsing_workflow` fails that
    resume with `ErrorCode.LLM_PROVIDER_ERROR` (see that class's docstring),
    which is exactly the pre-existing "no usable client" path. Hard-gating
    `POST /resumes` up front with a specific, actionable error is Task 7's
    job (docs/llm-provider-config/TECH.md), not this dependency's.

    Constructing the client performs no network call — every provider in
    this layer opens a connection only inside `.complete()` — so mounting
    this router still requires no reachable provider.
    """
    if user_id is None:
        return None
    try:
        return await get_user_llm_client(user_id)
    except LlmNotConfiguredError:
        logger.info(
            "No validated LLM configuration for user | user_id=%s", user_id
        )
        return None


def get_profile_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    llm_client: Annotated[LLMClient | None, Depends(get_llm_client)],
) -> ProfileService:
    return ProfileService(
        ResumeRepository(session),
        CandidateProfileRepository(session),
        producer=get_event_producer(),
        storage=get_resume_storage(),
        llm_client=llm_client,
    )


__all__ = [
    "get_event_producer",
    "get_llm_client",
    "get_optional_user_id",
    "get_profile_service",
    "get_resume_storage",
    "get_session",
]
