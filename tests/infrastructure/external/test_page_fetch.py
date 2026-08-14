"""Tests for infrastructure.external.page_fetch.PageFetchClient.

Mocks at the PageRenderer protocol boundary (the seam PlaywrightPageRenderer
implements) — no real browser is launched, consistent with Playwright
browser binaries not being installed in this environment.
"""

import asyncio

import pytest

from infrastructure.external.config import ExternalClientConfig, RetryPolicy
from infrastructure.external.errors import InvalidUrlError, PageFetchError
from infrastructure.external.page_fetch import FetchedPage, PageFetchClient


class _FakeRenderer:
    """PageRenderer fake: returns canned HTML, raises, or hangs, per test."""

    def __init__(self, *, html: str | None = None, error: Exception | None = None,
                 hang: bool = False, name: str = "fake") -> None:
        self.name = name
        self._html = html
        self._error = error
        self._hang = hang
        self.calls: list[str] = []

    async def render(self, url: str, *, timeout_seconds: float) -> str:
        self.calls.append(url)
        if self._hang:
            await asyncio.sleep(timeout_seconds + 10)
        if self._error is not None:
            raise self._error
        assert self._html is not None
        return self._html


FAST_CONFIG = ExternalClientConfig(
    retry=RetryPolicy(max_attempts=1, initial_backoff_seconds=0.001)
)


@pytest.mark.asyncio
async def test_fetch_returns_lightly_parsed_page_on_success():
    html = """
    <html>
      <head><title> Backend Engineer at Acme </title></head>
      <body>
        <script>trackingCode();</script>
        <style>.x { color: red; }</style>
        <h1>Backend Engineer</h1>
        <p>We build things.</p>
      </body>
    </html>
    """
    renderer = _FakeRenderer(html=html)
    client = PageFetchClient(renderer, FAST_CONFIG)

    page = await client.fetch("https://jobs.example.com/123")

    assert isinstance(page, FetchedPage)
    assert page.url == "https://jobs.example.com/123"
    assert page.html == html
    assert page.title == "Backend Engineer at Acme"
    assert "Backend Engineer" in page.text
    assert "We build things." in page.text
    # Non-content tags must not leak into the extracted text.
    assert "trackingCode" not in page.text
    assert "color: red" not in page.text
    assert page.fetched_at is not None


@pytest.mark.asyncio
async def test_fetch_does_not_decide_relevance_only_transports_content():
    # This layer must not filter, rank, or judge the page — it hands back
    # whatever content the renderer produced, unconditionally.
    html = "<html><body><p>Irrelevant unrelated content</p></body></html>"
    renderer = _FakeRenderer(html=html)
    client = PageFetchClient(renderer, FAST_CONFIG)

    page = await client.fetch("https://example.com/anything")

    assert "Irrelevant unrelated content" in page.text


@pytest.mark.asyncio
async def test_fetch_rejects_disallowed_scheme_without_calling_renderer():
    renderer = _FakeRenderer(html="<html></html>")
    client = PageFetchClient(renderer, FAST_CONFIG)

    with pytest.raises(InvalidUrlError) as exc_info:
        await client.fetch("ftp://example.com/file")

    assert exc_info.value.retryable is False
    assert renderer.calls == []  # never reached the transport


@pytest.mark.asyncio
async def test_fetch_rejects_url_without_host():
    renderer = _FakeRenderer(html="<html></html>")
    client = PageFetchClient(renderer, FAST_CONFIG)

    with pytest.raises(InvalidUrlError):
        await client.fetch("https://")

    assert renderer.calls == []


@pytest.mark.asyncio
async def test_fetch_normalizes_renderer_failure_to_page_fetch_error():
    renderer = _FakeRenderer(error=RuntimeError("navigation crashed"))
    client = PageFetchClient(renderer, FAST_CONFIG)

    with pytest.raises(PageFetchError) as exc_info:
        await client.fetch("https://jobs.example.com/broken")

    assert exc_info.value.provider == "fake"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_fetch_normalizes_timeout_to_page_fetch_error():
    renderer = _FakeRenderer(hang=True)
    config = ExternalClientConfig(
        timeout_seconds=0.02, retry=RetryPolicy(max_attempts=1)
    )
    client = PageFetchClient(renderer, config)

    with pytest.raises(PageFetchError) as exc_info:
        await client.fetch("https://jobs.example.com/slow")

    assert "timed out" in str(exc_info.value)


@pytest.mark.asyncio
async def test_fetch_raises_page_fetch_error_on_empty_document():
    renderer = _FakeRenderer(html="   ")
    client = PageFetchClient(renderer, FAST_CONFIG)

    with pytest.raises(PageFetchError) as exc_info:
        await client.fetch("https://jobs.example.com/empty")

    assert "empty" in str(exc_info.value)


@pytest.mark.asyncio
async def test_fetch_retries_transient_failure_then_succeeds():
    call_count = {"n": 0}

    class _FlakyRenderer:
        name = "flaky"

        async def render(self, url: str, *, timeout_seconds: float) -> str:
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise RuntimeError("temporary glitch")
            return "<html><body>ok</body></html>"

    config = ExternalClientConfig(
        retry=RetryPolicy(max_attempts=3, initial_backoff_seconds=0.001, backoff_multiplier=1.0)
    )
    client = PageFetchClient(_FlakyRenderer(), config)

    page = await client.fetch("https://jobs.example.com/flaky")

    assert call_count["n"] == 2
    assert "ok" in page.text


@pytest.mark.asyncio
async def test_fetch_title_is_none_when_absent():
    renderer = _FakeRenderer(html="<html><body><p>no title here</p></body></html>")
    client = PageFetchClient(renderer, FAST_CONFIG)

    page = await client.fetch("https://jobs.example.com/no-title")

    assert page.title is None


@pytest.mark.asyncio
async def test_fetch_page_convenience_method_returns_text_only():
    # Matches the PageFetcher protocol jobs/ingestion/extraction.py assumed
    # (fetch_page(url) -> str) so that caller can integrate without needing
    # to know about the FetchedPage envelope.
    html = "<html><head><title>T</title></head><body><p>Just the text.</p></body></html>"
    renderer = _FakeRenderer(html=html)
    client = PageFetchClient(renderer, FAST_CONFIG)

    text = await client.fetch_page("https://jobs.example.com/123")

    assert isinstance(text, str)
    assert "Just the text." in text


@pytest.mark.asyncio
async def test_fetch_page_raises_same_errors_as_fetch():
    renderer = _FakeRenderer(error=RuntimeError("boom"))
    client = PageFetchClient(renderer, FAST_CONFIG)

    with pytest.raises(PageFetchError):
        await client.fetch_page("https://jobs.example.com/broken")
