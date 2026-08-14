"""Playwright/BeautifulSoup page-fetch client.

Consumed by Job Ingestion Service (manual URL path) and available to Job
Discovery Service for scraped sources — see
docs/architecture/ownership.md#component--external-toolsapis-it-may-call.

This module decides *how* to fetch a URL, never *which* URL is worth
fetching or whether the posting behind it is relevant; that decision stays
with the calling component.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel

from infrastructure.external.config import ExternalClientConfig
from infrastructure.external.errors import InvalidUrlError, PageFetchError
from infrastructure.external.resilience import RateLimiter, call_with_resilience

ALLOWED_URL_SCHEMES = ("http", "https")

_NON_CONTENT_TAGS = ("script", "style", "noscript", "template", "svg")


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
    `PlaywrightPageRenderer` in production and by a fake in tests.
    """

    name: str

    async def render(self, url: str, *, timeout_seconds: float) -> str: ...


class PlaywrightPageRenderer:
    """Headless-Chromium renderer, so pages whose posting body is rendered
    client-side still yield content.

    Playwright is imported lazily so importing this module (and running the
    test suite) does not require browser binaries to be installed.
    """

    name = "playwright"

    def __init__(self, *, headless: bool = True, user_agent: str | None = None) -> None:
        self._headless = headless
        self._user_agent = user_agent
        self._playwright = None
        self._browser = None

    async def _ensure_browser(self):
        if self._browser is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless
            )
        return self._browser

    async def render(self, url: str, *, timeout_seconds: float) -> str:
        browser = await self._ensure_browser()
        context = await browser.new_context(
            **({"user_agent": self._user_agent} if self._user_agent else {})
        )
        try:
            page = await context.new_page()
            await page.goto(url, timeout=timeout_seconds * 1000, wait_until="load")
            return await page.content()
        finally:
            await context.close()

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


class PageFetchClient:
    """Fetches a URL and returns its raw HTML plus extracted visible text.

    All failures surface as `PageFetchError` (`JOB_FETCH_FAILED`), except an
    unusable URL which surfaces as `InvalidUrlError` (`INVALID_JOB_URL`) and
    is not retried.
    """

    def __init__(
        self,
        renderer: PageRenderer,
        config: ExternalClientConfig | None = None,
        *,
        allowed_schemes: Sequence[str] = ALLOWED_URL_SCHEMES,
    ) -> None:
        self._renderer = renderer
        self._config = config or ExternalClientConfig()
        self._allowed_schemes = tuple(allowed_schemes)
        self._rate_limiter = RateLimiter(self._config.min_interval_seconds)

    async def fetch(self, url: str) -> FetchedPage:
        self._validate_url(url)

        async def operation() -> str:
            return await self._renderer.render(
                url, timeout_seconds=self._config.timeout_seconds
            )

        html = await call_with_resilience(
            operation,
            config=self._config,
            error_type=PageFetchError,
            provider=self._renderer.name,
            description=f"page fetch of {url}",
            rate_limiter=self._rate_limiter,
        )
        return self._parse(url, html)

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

    def _parse(self, url: str, html: str) -> FetchedPage:
        if not html or not html.strip():
            raise PageFetchError(
                f"page fetch of {url} returned an empty document",
                provider=self._renderer.name,
            )
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:
            raise PageFetchError(
                f"could not parse HTML from {url}: {exc}",
                provider=self._renderer.name,
                cause=exc,
            ) from exc
        for tag in soup(list(_NON_CONTENT_TAGS)):
            tag.decompose()
        text = "\n".join(
            stripped
            for line in soup.get_text("\n").splitlines()
            if (stripped := line.strip())
        )
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
    "FetchedPage",
    "PageFetchClient",
    "PageRenderer",
    "PlaywrightPageRenderer",
]
