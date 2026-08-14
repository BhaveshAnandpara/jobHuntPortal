"""Job board search client wrapper.

Consumed by Job Discovery Service. This layer performs the query the caller
hands it and returns raw hits; it never decides what to search for, never
filters or ranks the hits, and never judges whether a posting is relevant —
per docs/architecture/service-boundaries.md#job-discovery-service that
decision belongs downstream, in Job Matching Service.
"""

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel

from infrastructure.external.config import ExternalClientConfig
from infrastructure.external.errors import JobSearchRequestError
from infrastructure.external.resilience import RateLimiter, call_with_resilience
from shared.types.enums import JobSourceType, RemoteWorkPreference


class JobSearchQuery(BaseModel):
    """Free-text search context supplied by the caller. Deliberately holds
    no profession-specific structure — `keywords` carries whatever the
    caller's profile/preferences imply, for any profession.
    """

    keywords: list[str] = []
    location: str | None = None
    remote_preference: RemoteWorkPreference | None = None
    max_results: int = 25
    provider_params: dict[str, str] = {}


class JobSearchHit(BaseModel):
    """One unranked, unfiltered posting as the provider returned it."""

    source_url: str
    source_type: JobSourceType
    provider: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    snippet: str | None = None


class JobBoardSearchProvider(Protocol):
    """A concrete job board/search backend."""

    name: str

    async def search(
        self, query: JobSearchQuery, *, timeout_seconds: float
    ) -> Sequence[JobSearchHit]: ...


class StaticJobBoardSearchProvider:
    """Provider backed by a fixed list of hits.

    Keeps the automatic-discovery path runnable locally and in tests with no
    network access or third-party credentials, until a real board
    integration is added alongside it.
    """

    def __init__(self, hits: Sequence[JobSearchHit], *, name: str = "static") -> None:
        self.name = name
        self._hits = list(hits)

    async def search(
        self, query: JobSearchQuery, *, timeout_seconds: float
    ) -> Sequence[JobSearchHit]:
        return self._hits[: query.max_results]


class JobBoardSearchClient:
    """Runs a `JobSearchQuery` against a provider under the shared timeout,
    retry, and rate-limit policy. Failures surface as
    `JobSearchRequestError` (`JOB_FETCH_FAILED`).
    """

    def __init__(
        self,
        provider: JobBoardSearchProvider,
        config: ExternalClientConfig | None = None,
    ) -> None:
        self._provider = provider
        self._config = config or ExternalClientConfig()
        self._rate_limiter = RateLimiter(self._config.min_interval_seconds)

    async def search(self, query: JobSearchQuery) -> list[JobSearchHit]:
        async def operation() -> Sequence[JobSearchHit]:
            return await self._provider.search(
                query, timeout_seconds=self._config.timeout_seconds
            )

        hits = await call_with_resilience(
            operation,
            config=self._config,
            error_type=JobSearchRequestError,
            provider=self._provider.name,
            description="job board search",
            rate_limiter=self._rate_limiter,
        )
        return list(hits)


__all__ = [
    "JobBoardSearchClient",
    "JobBoardSearchProvider",
    "JobSearchHit",
    "JobSearchQuery",
    "StaticJobBoardSearchProvider",
]
