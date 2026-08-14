"""People-search client wrapper.

Consumed by Contact Discovery Service. Returns raw people hits only —
classifying a hit into a `ContactType`, scoring it, or ordering the results
is Contact Discovery Service's ranking logic, not this layer's (see
docs/architecture/service-boundaries.md#contact-discovery-service).
"""

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel

from infrastructure.external.config import ExternalClientConfig
from infrastructure.external.errors import PeopleSearchRequestError
from infrastructure.external.resilience import RateLimiter, call_with_resilience


class PeopleSearchQuery(BaseModel):
    """Search context supplied by the caller. `role_keywords` is free text
    so the same client serves every profession — no role vocabulary is
    hard-coded here.
    """

    company: str
    role_keywords: list[str] = []
    location: str | None = None
    max_results: int = 25
    provider_params: dict[str, str] = {}


class PersonSearchHit(BaseModel):
    """One person as the provider returned them, before any classification
    or ranking by the caller.
    """

    full_name: str
    provider: str
    headline: str | None = None
    company: str | None = None
    profile_url: str | None = None
    email: str | None = None


class PeopleSearchProvider(Protocol):
    """A concrete people-search backend."""

    name: str

    async def search(
        self, query: PeopleSearchQuery, *, timeout_seconds: float
    ) -> Sequence[PersonSearchHit]: ...


class StaticPeopleSearchProvider:
    """Provider backed by a fixed list of hits, so Contact Discovery is
    runnable locally and in tests without third-party credentials.
    """

    def __init__(self, hits: Sequence[PersonSearchHit], *, name: str = "static") -> None:
        self.name = name
        self._hits = list(hits)

    async def search(
        self, query: PeopleSearchQuery, *, timeout_seconds: float
    ) -> Sequence[PersonSearchHit]:
        return self._hits[: query.max_results]


class PeopleSearchClient:
    """Runs a `PeopleSearchQuery` against a provider under the shared
    timeout, retry, and rate-limit policy. Failures surface as
    `PeopleSearchRequestError` (`CONTACT_SEARCH_FAILED`).
    """

    def __init__(
        self,
        provider: PeopleSearchProvider,
        config: ExternalClientConfig | None = None,
    ) -> None:
        self._provider = provider
        self._config = config or ExternalClientConfig()
        self._rate_limiter = RateLimiter(self._config.min_interval_seconds)

    async def search(self, query: PeopleSearchQuery) -> list[PersonSearchHit]:
        async def operation() -> Sequence[PersonSearchHit]:
            return await self._provider.search(
                query, timeout_seconds=self._config.timeout_seconds
            )

        hits = await call_with_resilience(
            operation,
            config=self._config,
            error_type=PeopleSearchRequestError,
            provider=self._provider.name,
            description="people search",
            rate_limiter=self._rate_limiter,
        )
        return list(hits)


__all__ = [
    "PeopleSearchClient",
    "PeopleSearchProvider",
    "PeopleSearchQuery",
    "PersonSearchHit",
    "StaticPeopleSearchProvider",
]
