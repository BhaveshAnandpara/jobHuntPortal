"""Playwright/BeautifulSoup page-fetch client.

Consumed by Job Ingestion Service (manual URL path) and available to Job
Discovery Service for scraped sources — see
docs/architecture/ownership.md#component--external-toolsapis-it-may-call.

This module decides *how* to fetch a URL, never *which* URL is worth
fetching or whether the posting behind it is relevant; that decision stays
with the calling component.

Fetch strategy: `PageFetchClient` tries a fast, no-JS static HTTP fetch
first (`StaticHttpRenderer`) and only pays for a headless-Chromium render
(`PlaywrightPageRenderer`) when the static result doesn't look like usable
page content — e.g. an SPA shell that returns `<div class="app"></div>`
with the real posting loaded client-side. `is_meaningful_content()` is the
deterministic (no LLM) heuristic that decides "insufficient, fall back."
"""

import asyncio
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag
from pydantic import BaseModel

from infrastructure.external.config import ExternalClientConfig
from infrastructure.external.errors import InvalidUrlError, PageFetchError
from infrastructure.external.resilience import RateLimiter, call_with_resilience
from infrastructure.logging import format_context, get_logger

logger = get_logger(__name__)

ALLOWED_URL_SCHEMES = ("http", "https")

_NON_CONTENT_TAGS = ("script", "style", "noscript", "template", "svg")

MIN_MEANINGFUL_TEXT_LENGTH = 200
"""Below this many characters, treat the page as an SPA shell/placeholder
rather than real content — a generic "Candidate Experience page" or empty
app shell is typically well under this."""

MIN_TEXT_TO_TITLE_RATIO = 3.0
"""Extracted body text shorter than `title * this ratio` is treated as
"basically just the title" — e.g. a page whose only text is its own
`<title>` repeated in an h1, with everything else rendered by JS."""

_CONTENT_WAIT_TIMEOUT_MS = 8_000
"""Bounded wait (state-based, not a blind sleep) for rendered content to
reach `MIN_MEANINGFUL_TEXT_LENGTH` after navigation — see
`PlaywrightPageRenderer._render_sync`. Capped separately from the overall
per-request timeout so a slow-to-settle page still yields whatever
rendered in time rather than failing the whole fetch."""


def _extract_text(node: Tag | BeautifulSoup) -> str:
    """Visible text of `node`, one non-empty line per line — shared by both
    the static and (main-preferring) rendered parse paths. `_NON_CONTENT_TAGS`
    must already be stripped from the tree `node` belongs to before calling
    this, so hidden script/style text is never included."""
    return "\n".join(
        stripped for line in node.get_text("\n").splitlines() if (stripped := line.strip())
    )


def is_meaningful_content(text: str, title: str | None) -> bool:
    """Deterministic, LLM-free check for "does this look like it could be a
    real job posting, or just page chrome/an SPA shell". Used only to
    decide whether the Playwright fallback is worth paying for — judging
    whether a posting is relevant/well-formed stays downstream (LLM
    extraction), never here. Intentionally simple: a minimum length, and a
    minimum amount of content beyond the title itself.
    """
    stripped = text.strip()
    if len(stripped) < MIN_MEANINGFUL_TEXT_LENGTH:
        return False
    title_stripped = (title or "").strip()
    return not title_stripped or len(stripped) >= len(title_stripped) * MIN_TEXT_TO_TITLE_RATIO


class FetchedPage(BaseModel):
    """Raw + lightly-parsed page content handed to the caller's extraction
    step. Deliberately not a domain type: turning this into a
    `NormalizedJob` is Job Ingestion Service's job, not this layer's.
    """

    url: str
    html: str
    text: str
    title: str | None = None
    fetched_at: datetime


class PageRenderer(Protocol):
    """Transport that turns a URL into rendered HTML. Implemented by
    `StaticHttpRenderer` (primary) and `PlaywrightPageRenderer` (fallback)
    in production, and by a fake in tests.
    """

    name: str

    async def render(self, url: str, *, timeout_seconds: float) -> str: ...


class StaticHttpRenderer:
    """Primary renderer: a plain HTTP GET, no JavaScript execution.

    Tried first for every fetch — most job postings are readable straight
    off the server-rendered HTML, and this avoids paying Chromium's launch
    cost on every request. `PageFetchClient` only falls back to
    `PlaywrightPageRenderer` when this renderer's result fails
    `is_meaningful_content()` (e.g. an SPA shell whose real content loads
    via client-side JS) or raises.
    """

    name = "static-http"

    def __init__(self, *, user_agent: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        self._user_agent = user_agent
        # `client` is injectable for tests (`httpx.MockTransport`); production
        # callers leave this `None` and get a fresh short-lived client per call.
        self._client = client

    async def render(self, url: str, *, timeout_seconds: float) -> str:
        headers = {"User-Agent": self._user_agent} if self._user_agent else {}
        if self._client is not None:
            response = await self._client.get(url, headers=headers, timeout=timeout_seconds)
            response.raise_for_status()
            return response.text
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_seconds) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text


class PlaywrightPageRenderer:
    """Fallback renderer only: headless Chromium, used when
    `StaticHttpRenderer`'s result doesn't look like usable content (see
    module docstring) — so pages whose posting body is rendered
    client-side still yield content, without paying Chromium's cost on
    every fetch.

    Playwright is imported lazily so importing this module (and running the
    test suite) does not require browser binaries to be installed.

    Uses Playwright's *sync* API, driven inside a worker thread via
    `asyncio.to_thread`, rather than its async API on the caller's own
    event loop. Playwright's async API launches its Node driver via
    `asyncio.create_subprocess_exec`, which only Windows' `ProactorEventLoop`
    supports; this project's real server (`scripts/run_server.py`) must run
    on a `SelectorEventLoop` instead, for `psycopg`'s async Postgres driver
    (see that script's docstring) — the two requirements are incompatible on
    the *same* loop. The sync API sidesteps this: called with no event loop
    already running in the current thread (true for a `to_thread` worker),
    it creates its own fresh loop for that thread via `asyncio.new_event_loop()`,
    which still resolves to a `ProactorEventLoop` since only this thread's
    loop *instance* was overridden for `psycopg`, never the process-wide
    event loop *policy* — so Playwright's subprocess launch works there.
    Each call starts and tears down its own browser rather than reusing one
    across calls, since a Playwright sync-API browser/connection is bound to
    the specific OS thread that created it, and `to_thread`'s executor does
    not guarantee the same worker thread on every call. A reusable-instance
    pool would need a dedicated long-lived thread (or the async API on its
    own loop) to own the browser safely — a larger change than this fetch
    strategy needs, and far less costly now that this renderer only runs as
    a fallback rather than on every single fetch.
    """

    name = "playwright"

    def __init__(self, *, headless: bool = True, user_agent: str | None = None) -> None:
        self._headless = headless
        self._user_agent = user_agent

    def _render_sync(self, url: str, timeout_seconds: float) -> str:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self._headless)
            try:
                context = browser.new_context(
                    **({"user_agent": self._user_agent} if self._user_agent else {})
                )
                try:
                    page = context.new_page()
                    # `domcontentloaded` rather than `load`: the goal is to
                    # start interacting with the DOM as soon as it exists,
                    # not to wait for every subresource (images, fonts,
                    # analytics beacons) — the explicit wait below is what
                    # actually determines "is there real content yet".
                    page.goto(url, timeout=timeout_seconds * 1000, wait_until="domcontentloaded")
                    wait_timeout_ms = min(_CONTENT_WAIT_TIMEOUT_MS, timeout_seconds * 1000)
                    try:
                        page.wait_for_function(
                            "() => document.body && "
                            f"document.body.innerText.trim().length >= {MIN_MEANINGFUL_TEXT_LENGTH}",
                            timeout=wait_timeout_ms,
                        )
                    except PlaywrightTimeoutError:
                        # Bounded, state-based wait per this renderer's
                        # contract — if the page never reaches the
                        # threshold in time, extract whatever rendered
                        # rather than failing the whole fetch; a genuinely
                        # empty/broken page still gets caught downstream by
                        # `is_meaningful_content` on the result.
                        pass
                    return page.content()
                finally:
                    context.close()
            finally:
                browser.close()

    async def render(self, url: str, *, timeout_seconds: float) -> str:
        return await asyncio.to_thread(self._render_sync, url, timeout_seconds)


class PageFetchClient:
    """Fetches a URL and returns its raw HTML plus extracted visible text.

    `renderer` is tried first (in production, `StaticHttpRenderer` — fast,
    no browser). If `fallback_renderer` is given (in production,
    `PlaywrightPageRenderer`) and either `renderer` fails or its result
    fails `is_meaningful_content()`, `fallback_renderer` is tried once and
    its result used instead. `fallback_renderer` defaults to `None`, in
    which case this behaves exactly as a single-renderer client always
    has — no quality check, whatever `renderer` returns is used as-is.

    All failures surface as `PageFetchError` (`JOB_FETCH_FAILED`), except an
    unusable URL which surfaces as `InvalidUrlError` (`INVALID_JOB_URL`) and
    is not retried.
    """

    def __init__(
        self,
        renderer: PageRenderer,
        config: ExternalClientConfig | None = None,
        *,
        fallback_renderer: PageRenderer | None = None,
        allowed_schemes: Sequence[str] = ALLOWED_URL_SCHEMES,
    ) -> None:
        self._renderer = renderer
        self._fallback_renderer = fallback_renderer
        self._config = config or ExternalClientConfig()
        self._allowed_schemes = tuple(allowed_schemes)
        self._rate_limiter = RateLimiter(self._config.min_interval_seconds)

    async def fetch(self, url: str) -> FetchedPage:
        self._validate_url(url)

        primary_error: PageFetchError | None = None
        primary_page: FetchedPage | None = None
        try:
            html = await self._render(self._renderer, url)
            primary_page = self._parse(url, html, provider=self._renderer.name)
        except PageFetchError as exc:
            primary_error = exc

        if self._fallback_renderer is None:
            if primary_error is not None:
                raise primary_error
            assert primary_page is not None
            return primary_page

        if primary_page is not None and is_meaningful_content(primary_page.text, primary_page.title):
            logger.info(
                "Static extraction succeeded | %s",
                format_context(url=url, extraction_mode="static", chars=len(primary_page.text)),
            )
            return primary_page

        insufficiency_reason = (
            str(primary_error) if primary_error is not None else "content did not look like a real posting"
        )
        logger.info(
            "Static extraction insufficient | %s",
            format_context(url=url, falling_back_to=self._fallback_renderer.name, reason=insufficiency_reason),
        )

        started = time.monotonic()
        try:
            rendered_html = await self._render(self._fallback_renderer, url)
        except PageFetchError as fallback_error:
            raise PageFetchError(
                f"page fetch of {url} failed: static extraction was insufficient "
                f"({insufficiency_reason}) and browser fallback "
                f"({self._fallback_renderer.name}) also failed: {fallback_error}",
                provider=self._fallback_renderer.name,
                cause=fallback_error,
            ) from fallback_error

        rendered_page = self._parse(url, rendered_html, provider=self._fallback_renderer.name, prefer_main=True)
        duration_ms = round((time.monotonic() - started) * 1000, 1)
        logger.info(
            "Rendered extraction succeeded | %s",
            format_context(
                url=url, extraction_mode="browser", chars=len(rendered_page.text), duration_ms=duration_ms
            ),
        )
        return rendered_page

    async def _render(self, renderer: PageRenderer, url: str) -> str:
        async def operation() -> str:
            return await renderer.render(url, timeout_seconds=self._config.timeout_seconds)

        return await call_with_resilience(
            operation,
            config=self._config,
            error_type=PageFetchError,
            provider=renderer.name,
            description=f"page fetch of {url}",
            rate_limiter=self._rate_limiter,
        )

    async def fetch_page(self, url: str) -> str:
        """Convenience wrapper returning only the extracted visible text.

        Equivalent to `(await self.fetch(url)).text`. Added to match the
        `PageFetcher` protocol Job Ingestion Service's extraction step
        assumed (see `jobs/ingestion/extraction.py`'s `PageFetcher` docstring
        — "Assumed interface — reconcile with `infrastructure.external`
        once the External Integrations Agent lands it") — the raw+lightly-
        parsed page is exactly what that extraction prompt wants, without
        the caller having to know about `FetchedPage`. Prefer `fetch()`
        directly for anything that also needs `html`/`title`/`fetched_at`.
        Raises the same `PageFetchError`/`InvalidUrlError` as `fetch()`.
        """
        page = await self.fetch(url)
        return page.text

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in self._allowed_schemes or not parsed.netloc:
            raise InvalidUrlError(
                f"URL must be one of {self._allowed_schemes} with a host: {url!r}",
                provider=self._renderer.name,
            )

    def _parse(self, url: str, html: str, *, provider: str, prefer_main: bool = False) -> FetchedPage:
        if not html or not html.strip():
            raise PageFetchError(
                f"page fetch of {url} returned an empty document",
                provider=provider,
            )
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:
            raise PageFetchError(
                f"could not parse HTML from {url}: {exc}",
                provider=provider,
                cause=exc,
            ) from exc
        for tag in soup(list(_NON_CONTENT_TAGS)):
            tag.decompose()

        text_source: Tag | BeautifulSoup = soup
        if prefer_main:
            main = soup.find("main")
            if isinstance(main, Tag) and _extract_text(main):
                text_source = main

        text = _extract_text(text_source)
        title = soup.title.get_text(strip=True) if soup.title else None
        return FetchedPage(
            url=url,
            html=html,
            text=text,
            title=title or None,
            fetched_at=datetime.now(UTC),
        )


__all__ = [
    "ALLOWED_URL_SCHEMES",
    "MIN_MEANINGFUL_TEXT_LENGTH",
    "MIN_TEXT_TO_TITLE_RATIO",
    "FetchedPage",
    "PageFetchClient",
    "PageRenderer",
    "PlaywrightPageRenderer",
    "StaticHttpRenderer",
    "is_meaningful_content",
]
