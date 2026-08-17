"""People-search client wrapper.

Consumed by Contact Discovery Service. Returns raw people hits only —
classifying a hit into a `ContactType`, scoring it, or ordering the results
is Contact Discovery Service's ranking logic, not this layer's (see
docs/architecture/service-boundaries.md#contact-discovery-service).

Three network-backed providers, all proxies onto real Google Search results
and therefore sharing the same `site:`-restricted query shape
(`_SiteRestrictedSearchProvider`): `PublicWebSearchProvider` (Google's own
Programmable Search Engine / Custom Search JSON API), `SerpApiProvider`
(serpapi.com), and `SerperProvider` (google.serper.dev). Google's own API is
closed to new customers as of 2025 and is being retired outright on
2027-01-01 (https://developers.google.com/custom-search/v1/overview) — the
other two exist as viable alternatives for a project started after that
cutoff; see each class's docstring for its own free-tier terms.
`default_people_search_provider()` resolves which one Contact Discovery
Service uses from `PEOPLE_SEARCH_PROVIDER`/env, mirroring
`infrastructure.llm.structured._default_provider`'s pattern, and falls back
to the existing zero-credential `StaticPeopleSearchProvider([])` when
unconfigured.
"""

import os
import re
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from infrastructure.external.config import ExternalClientConfig
from infrastructure.external.errors import PeopleSearchRequestError
from infrastructure.external.resilience import RateLimiter, call_with_resilience
from infrastructure.logging import format_context, get_logger

logger = get_logger(__name__)


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


_GOOGLE_CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
_MAX_SUBQUERIES = 3
"""One search-provider call per `role_keywords` entry would burn through a
free daily quota fast (Google CSE's free tier is 100 queries/day total,
shared across every job this process discovers contacts for) — bounded to
the first few keywords `build_search_plan` proposed, which are already
ordered by relevance."""

_TITLE_TRAILING_SITE_RE = re.compile(r"\s*\|\s*LinkedIn\s*$", re.IGNORECASE)


def _parse_search_item(item: dict, *, provider: str) -> PersonSearchHit | None:
    """Conservative normalization of one raw search-result item into a
    `PersonSearchHit`. LinkedIn's own public result titles follow a
    "Name - Headline - Company" convention closely enough to split on, but
    only when the shape is unambiguous — see module docstring's "if
    uncertain, leave fields null rather than guessing" (this task's own
    brief, section 9).
    """
    title = (item.get("title") or "").strip()
    if not title:
        return None  # nothing usable to build even a name from
    link = (item.get("link") or "").strip() or None
    snippet = (item.get("snippet") or "").strip() or None

    display_title = _TITLE_TRAILING_SITE_RE.sub("", title).strip()
    parts = [p.strip() for p in display_title.split(" - ") if p.strip()]

    if len(parts) >= 2:
        full_name = parts[0]
        if len(parts) >= 3:
            headline = " - ".join(parts[1:-1])
            company = parts[-1]
        else:
            headline = parts[1]
            company = None
    else:
        # Doesn't match the expected shape — use the literal title as the
        # name (real data from the result, not a guess) and leave
        # headline/company null rather than mis-splitting it.
        full_name = display_title
        headline = None
        company = None

    return PersonSearchHit(
        full_name=full_name,
        provider=provider,
        headline=headline or snippet,
        company=company,
        profile_url=link,
        email=None,
    )


def _normalized_profile_url(url: str) -> str:
    """Scheme/case/trailing-slash/query/fragment-insensitive form of a
    profile URL, so `https://www.linkedin.com/in/jane-doe/` and
    `http://linkedin.com/in/jane-doe?trk=x` dedupe as the same person."""
    parsed = urlsplit(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/").lower()
    return f"{host}{path}"


def _dedupe_key(hit: PersonSearchHit) -> tuple[str, str] | None:
    """`None` means "not enough stable signal to safely dedupe" — such a
    hit is always kept, per this task's brief (section 10): never merge
    distinct people on name alone."""
    if hit.profile_url:
        return ("url", _normalized_profile_url(hit.profile_url))
    if hit.company:
        return ("name+company", f"{hit.full_name.strip().casefold()}|{hit.company.strip().casefold()}")
    return None


def deduplicate_hits(hits: Sequence[PersonSearchHit]) -> list[PersonSearchHit]:
    """Primary key: normalized `profile_url`. Secondary (only when no URL):
    `full_name` + `company`, since name alone is not a safe identity
    signal. A hit with neither is never deduped against anything."""
    seen: set[tuple[str, str]] = set()
    deduped: list[PersonSearchHit] = []
    for hit in hits:
        key = _dedupe_key(hit)
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        deduped.append(hit)
    return deduped


def _primary_location(location: str) -> str:
    """Reduces `location` to just its first comma-separated segment before
    it goes into an unquoted (loose, bag-of-words) part of the search
    query. Google ANDs every unquoted word together, so a location that is
    actually a multi-region list — e.g. a remote-first job posting's
    "United States & Canada, India, United Kingdom, Brazil, European
    Union" — would otherwise demand all ~9 of those words appear on a
    single LinkedIn profile simultaneously, which is effectively
    unsatisfiable and silently zeroes out the search. Using only the
    primary region keeps location as the soft relevance signal it was
    always meant to be (unlike `company`/`role_keyword`, it's never quoted
    as a hard requirement) without the multi-region case defeating the
    search entirely. A single-value location (the common case — "Remote",
    "San Francisco, CA") passes through unchanged aside from the split.
    """
    return location.split(",")[0].strip()


class _SiteRestrictedSearchProvider:
    """Shared query-building, role-keyword fan-out, and dedup logic for any
    provider that accepts a free-text `q` string with `site:` operator
    support and returns items shaped like Google's own organic results
    (`title`/`link`/`snippet`) — true of `PublicWebSearchProvider`,
    `SerpApiProvider`, and `SerperProvider` alike, since all three proxy
    real Google Search results. A subclass implements only `_search_one`
    (the provider-specific endpoint, auth, and response envelope) and sets
    `name`; everything else — building the `site:`-restricted query per
    `role_keywords` entry (bounded by `_MAX_SUBQUERIES`), running those
    sequentially (not concurrently — gentler on the provider's rate limit),
    parsing each raw item via `_parse_search_item`, and deduplicating
    across subqueries via `deduplicate_hits` — is identical across all
    three and lives here once.

    SEARCH only, per this component's architecture boundary
    (docs/architecture/service-boundaries.md#contact-discovery-service):
    no classification, scoring, or ranking happens here, and nothing about
    a specific profession is ever referenced — `role_keywords` (from
    `PeopleSearchQuery`, itself produced by Contact Discovery's own LLM
    query planning) is opaque free text plugged into the query string.
    """

    name: str

    def __init__(
        self,
        *,
        site_restriction: str | None = "linkedin.com/in",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._site_restriction = site_restriction
        # Injectable for tests (`httpx.MockTransport`); production callers
        # leave this `None` and get a fresh short-lived client per call.
        self._client = client

    def _build_query_string(self, query: PeopleSearchQuery, role_keyword: str) -> str:
        site = query.provider_params.get("site", self._site_restriction)
        parts: list[str] = []
        if site:
            parts.append(f"site:{site}")
        parts.append(f'"{query.company}"')
        if role_keyword:
            parts.append(f'"{role_keyword}"')
        if query.location:
            parts.append(_primary_location(query.location))
        return " ".join(parts)

    async def _search_one(
        self, client: httpx.AsyncClient, query_string: str, num_results: int, timeout_seconds: float
    ) -> list[dict]:
        raise NotImplementedError

    async def search(
        self, query: PeopleSearchQuery, *, timeout_seconds: float
    ) -> Sequence[PersonSearchHit]:
        role_keywords = query.role_keywords[:_MAX_SUBQUERIES] or [""]

        async def run(client: httpx.AsyncClient) -> list[PersonSearchHit]:
            # Any failure here propagates up to `PeopleSearchClient`'s
            # existing `call_with_resilience` wrapper — no subclass
            # implements its own retry loop (this task's brief, section
            # 13: avoid a second hidden retry system).
            raw_items: list[dict] = []
            for role_keyword in role_keywords:
                query_string = self._build_query_string(query, role_keyword)
                raw_items.extend(
                    await self._search_one(client, query_string, query.max_results, timeout_seconds)
                )
            hits = [
                hit
                for item in raw_items
                if (hit := _parse_search_item(item, provider=self.name)) is not None
            ]
            return deduplicate_hits(hits)[: query.max_results]

        if self._client is not None:
            return await run(self._client)
        async with httpx.AsyncClient() as client:
            return await run(client)


def _log_and_raise_on_error_response(
    response: httpx.Response, *, label: str, query_string: str
) -> None:
    """Shared failure-path logging for every `_SiteRestrictedSearchProvider`
    subclass's `_search_one`. Without this, a non-2xx (bad key, quota
    exceeded, malformed query, project not enrolled in the API, ...) reaches
    `call_with_resilience` as a bare exception and gets retried/normalized
    into `PeopleSearchRequestError` with no record of what the provider
    actually said — the request log shows what was sent, but never what
    came back, and an HTTP failure becomes indistinguishable from a
    legitimate zero-result search once it reaches Contact Discovery's own
    "no contacts found" log line. Body is truncated: an error payload this
    small is not user data, but this is still the request/response log
    site so the same "don't dump unbounded text" convention applies
    (infrastructure.logging.config.format_context's docstring).
    """
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        logger.warning(
            f"{label} response | %s",
            format_context(query=query_string, status=response.status_code, body=response.text[:500]),
        )
        raise


class PublicWebSearchProvider(_SiteRestrictedSearchProvider):
    """Public web search via Google's Programmable Search Engine (Custom
    Search JSON API) — supports `site:`-restricted queries natively,
    matching this provider's default of scoping to public LinkedIn profile
    pages (`site:linkedin.com/in`), overridable per-call via
    `PeopleSearchQuery.provider_params["site"]`.

    Closed to new customers as of 2025, and Google is retiring the API
    entirely on 2027-01-01
    (https://developers.google.com/custom-search/v1/overview) — only
    usable here if the underlying Google Cloud project already had access
    before the cutoff; a project created after it gets `403
    PERMISSION_DENIED` on every call regardless of query, which surfaces
    via `_log_and_raise_on_error_response`'s failure-path logging rather
    than silently. `SerpApiProvider`/`SerperProvider` are this project's
    alternatives for a project that never had access to begin with.

    Public data only: this only ever issues an unauthenticated HTTP GET to
    Google's search API and reads back the public snippet the search
    engine already indexed. It never logs into LinkedIn, never renders an
    authenticated page, and never bypasses a CAPTCHA or access control —
    if a page isn't publicly indexed with useful content, it's simply
    absent from these results.
    """

    name = "public-web-search"

    def __init__(
        self,
        *,
        api_key: str,
        engine_id: str,
        site_restriction: str | None = "linkedin.com/in",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(site_restriction=site_restriction, client=client)
        self._api_key = api_key
        self._engine_id = engine_id

    async def _search_one(
        self, client: httpx.AsyncClient, query_string: str, num_results: int, timeout_seconds: float
    ) -> list[dict]:
        # Query/response visibility for verifying live CSE configuration
        # (e.g. "Sites to search" scope, API enablement) against what
        # actually comes back — never the api_key, and only titles (not
        # full snippets/links) from the response, per this module's "never
        # log full text" logging convention.
        logger.info(
            "Google CSE request | %s",
            format_context(query=query_string, cx=self._engine_id, num=max(1, min(10, num_results))),
        )
        response = await client.get(
            _GOOGLE_CSE_ENDPOINT,
            params={
                "key": self._api_key,
                "cx": self._engine_id,
                "q": query_string,
                "num": max(1, min(10, num_results)),
            },
            timeout=timeout_seconds,
        )
        _log_and_raise_on_error_response(response, label="Google CSE", query_string=query_string)
        payload = response.json()
        items = payload.get("items")
        items = items if isinstance(items, list) else []
        logger.info(
            "Google CSE response | %s",
            format_context(
                query=query_string,
                status=response.status_code,
                item_count=len(items),
                search_information=payload.get("searchInformation", {}).get("totalResults"),
                titles=[item.get("title") for item in items] or None,
            ),
        )
        return items


_SERPAPI_ENDPOINT = "https://serpapi.com/search.json"


class SerpApiProvider(_SiteRestrictedSearchProvider):
    """serpapi.com's `google` engine — a real Google Search proxy with the
    same `site:`-restricted query support as `PublicWebSearchProvider`, and
    a genuinely recurring free tier (250 searches/month, capped at
    50/hour) as of this writing — unlike Google's own Custom Search JSON
    API, which is closed to new customers (see `PublicWebSearchProvider`'s
    docstring). Response shape (`organic_results[].title/link/snippet`) is
    close enough to Google CSE's own `items[]` that this reuses
    `_parse_search_item` unchanged via the shared base class.

    Auth is a `api_key` query param (like Google CSE's `key`), no separate
    engine/cx id — only `PEOPLE_SEARCH_API_KEY` is required.
    """

    name = "serpapi"

    def __init__(
        self,
        *,
        api_key: str,
        site_restriction: str | None = "linkedin.com/in",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(site_restriction=site_restriction, client=client)
        self._api_key = api_key

    async def _search_one(
        self, client: httpx.AsyncClient, query_string: str, num_results: int, timeout_seconds: float
    ) -> list[dict]:
        logger.info(
            "SerpApi request | %s",
            format_context(query=query_string, num=max(1, min(10, num_results))),
        )
        response = await client.get(
            _SERPAPI_ENDPOINT,
            params={
                "engine": "google",
                "q": query_string,
                "num": max(1, min(10, num_results)),
                "api_key": self._api_key,
            },
            timeout=timeout_seconds,
        )
        _log_and_raise_on_error_response(response, label="SerpApi", query_string=query_string)
        payload = response.json()
        items = payload.get("organic_results")
        items = items if isinstance(items, list) else []
        logger.info(
            "SerpApi response | %s",
            format_context(
                query=query_string,
                status=response.status_code,
                item_count=len(items),
                titles=[item.get("title") for item in items] or None,
            ),
        )
        return items


_SERPER_ENDPOINT = "https://google.serper.dev/search"


class SerperProvider(_SiteRestrictedSearchProvider):
    """google.serper.dev's `/search` endpoint — another real Google Search
    proxy with the same `site:`-restricted query support. A larger
    one-time free allowance (2,500 queries, no card required) rather than
    SerpApi's smaller-but-recurring monthly quota, and by far the
    cheapest paid rate of the three providers in this module if usage
    ever needs to scale past that (see `PublicWebSearchProvider`'s
    docstring for why Google's own API is no longer a safe default for a
    new project). Response shape (`organic[].title/link/snippet`) again
    matches Google CSE's own `items[]` closely enough to reuse
    `_parse_search_item` unchanged.

    Auth is a request header (`X-API-KEY`), not a query param, and the
    call is a POST with a JSON body rather than a GET with query
    params — the one structural difference from the other two providers
    in this module; only `PEOPLE_SEARCH_API_KEY` is required.
    """

    name = "serper"

    def __init__(
        self,
        *,
        api_key: str,
        site_restriction: str | None = "linkedin.com/in",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(site_restriction=site_restriction, client=client)
        self._api_key = api_key

    async def _search_one(
        self, client: httpx.AsyncClient, query_string: str, num_results: int, timeout_seconds: float
    ) -> list[dict]:
        logger.info(
            "Serper request | %s",
            format_context(query=query_string, num=max(1, min(10, num_results))),
        )
        response = await client.post(
            _SERPER_ENDPOINT,
            headers={"X-API-KEY": self._api_key, "Content-Type": "application/json"},
            json={"q": query_string, "num": max(1, min(10, num_results))},
            timeout=timeout_seconds,
        )
        _log_and_raise_on_error_response(response, label="Serper", query_string=query_string)
        payload = response.json()
        items = payload.get("organic")
        items = items if isinstance(items, list) else []
        logger.info(
            "Serper response | %s",
            format_context(
                query=query_string,
                status=response.status_code,
                item_count=len(items),
                titles=[item.get("title") for item in items] or None,
            ),
        )
        return items


def default_people_search_provider() -> PeopleSearchProvider:
    """Resolve which `PeopleSearchProvider` Contact Discovery Service uses
    by default, from env — mirrors
    `infrastructure.llm.structured._default_provider`'s pattern.

    `PEOPLE_SEARCH_PROVIDER` selects the implementation:

    - `"google-cse"` -> `PublicWebSearchProvider`. Needs
      `PEOPLE_SEARCH_API_KEY` + `PEOPLE_SEARCH_ENGINE_ID`. See that class's
      docstring — closed to new customers, only usable with a pre-existing
      Google Cloud project that already had access.
    - `"serpapi"` -> `SerpApiProvider`. Needs only `PEOPLE_SEARCH_API_KEY`.
      Genuinely recurring free tier (250 searches/month).
    - `"serper"` -> `SerperProvider`. Needs only `PEOPLE_SEARCH_API_KEY`.
      Larger one-time free allowance (2,500 queries) instead of a
      recurring quota; cheapest paid rate of the three past that.
    - unset/anything else -> `StaticPeopleSearchProvider([])`, this
      project's existing zero-credential default (see
      `scripts/run_consumers.py`'s docstring).

    Missing the credential(s) a selected provider needs falls back to the
    static provider too, with a warning logged rather than a crash,
    matching this component's existing tolerance for "zero contacts
    found" as a non-error outcome.
    """
    provider_name = os.environ.get("PEOPLE_SEARCH_PROVIDER", "").strip().lower()
    api_key = os.environ.get("PEOPLE_SEARCH_API_KEY", "").strip()

    if provider_name == "google-cse":
        engine_id = os.environ.get("PEOPLE_SEARCH_ENGINE_ID", "").strip()
        if not api_key or not engine_id:
            logger.warning(
                "PEOPLE_SEARCH_PROVIDER=google-cse but PEOPLE_SEARCH_API_KEY/"
                "PEOPLE_SEARCH_ENGINE_ID is not set — falling back to zero-hit static provider"
            )
            return StaticPeopleSearchProvider([])
        return PublicWebSearchProvider(api_key=api_key, engine_id=engine_id)

    if provider_name == "serpapi":
        if not api_key:
            logger.warning(
                "PEOPLE_SEARCH_PROVIDER=serpapi but PEOPLE_SEARCH_API_KEY is not set — "
                "falling back to zero-hit static provider"
            )
            return StaticPeopleSearchProvider([])
        return SerpApiProvider(api_key=api_key)

    if provider_name == "serper":
        if not api_key:
            logger.warning(
                "PEOPLE_SEARCH_PROVIDER=serper but PEOPLE_SEARCH_API_KEY is not set — "
                "falling back to zero-hit static provider"
            )
            return StaticPeopleSearchProvider([])
        return SerperProvider(api_key=api_key)

    return StaticPeopleSearchProvider([])


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
    "PublicWebSearchProvider",
    "SerpApiProvider",
    "SerperProvider",
    "StaticPeopleSearchProvider",
]
