"""FastAPI dependency wiring for Job Discovery Service's `/job-sources`
router, plus factory helpers whoever calls
`jobs.discovery.service.run_discovery` (a scheduler — there is no public
API trigger, per component-contracts.md) can use to build its dependencies.

Everything that touches another layer (database, Kafka, the External
Integrations Layer, the LLM Provider Layer, or another component's HTTP
API) is imported lazily, mirroring `jobs.ingestion.dependencies`. The page
fetcher and structured extractor factories are literally re-exported from
`jobs.ingestion.dependencies` rather than redefined here — same adapters,
same production wiring, both subpackages of this one component.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.external.job_search import JobBoardSearchClient
from infrastructure.kafka.producer import EventProducer
from jobs.discovery.clients import ProfileServiceClient, UserPreferencesClient
from jobs.ingestion.dependencies import get_page_fetcher, get_structured_extractor
from jobs.repository import JobRepository, JobSourceRepository


async def get_db_session() -> AsyncIterator[AsyncSession]:
    from infrastructure.database import get_session

    async for session in get_session():
        yield session


def get_job_source_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JobSourceRepository:
    return JobSourceRepository(session)


def get_job_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JobRepository:
    return JobRepository(session)


_producer: EventProducer | None = None


def get_event_producer() -> EventProducer:
    """Process-wide `EventProducer` for the discovery run's publish step.
    Not used by the `/job-sources` CRUD routes themselves.
    """
    global _producer
    if _producer is None:
        _producer = EventProducer("job-discovery-service")
    return _producer


def get_search_client() -> JobBoardSearchClient:
    """Default production job board search client.

    Backed by `StaticJobBoardSearchProvider` (an empty hit list) until a
    real job-board provider is configured — the External Integrations Layer
    documents this as the intended local/dev default (no third-party
    credentials required to run the platform locally, per
    about_project.md's "minimal cost" goal).
    """
    from infrastructure.external.job_search import StaticJobBoardSearchProvider

    return JobBoardSearchClient(StaticJobBoardSearchProvider([]))


def get_user_preferences_client() -> UserPreferencesClient:
    return UserPreferencesClient()


def get_profile_service_client() -> ProfileServiceClient:
    return ProfileServiceClient()


__all__ = [
    "get_db_session",
    "get_event_producer",
    "get_job_repository",
    "get_job_source_repository",
    "get_page_fetcher",
    "get_profile_service_client",
    "get_search_client",
    "get_structured_extractor",
    "get_user_preferences_client",
]
