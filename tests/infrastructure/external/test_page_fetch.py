"""Tests for infrastructure.external.page_fetch.PageFetchClient.

Mocks at the PageRenderer protocol boundary (the seam PlaywrightPageRenderer
implements) — no real browser is launched, consistent with Playwright
browser binaries not being installed in this environment.
"""

import asyncio

import httpx
import pytest

from infrastructure.external.config import ExternalClientConfig, RetryPolicy
from infrastructure.external.errors import InvalidUrlError, PageFetchError
from infrastructure.external.page_fetch import (
    FetchedPage,
    PageFetchClient,
    StaticHttpRenderer,
    is_meaningful_content,
)


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


# ---------------------------------------------------------------------------
# Static-first, Chromium-fallback strategy
# ---------------------------------------------------------------------------

# A real-looking static job posting: long, and far more text than the title.
_MEANINGFUL_HTML = f"""
<html>
  <head><title>Backend Engineer at Acme</title></head>
  <body>
    <h1>Backend Engineer</h1>
    <p>{"We build distributed systems that scale. " * 10}</p>
  </body>
</html>
"""

# An SPA shell: page.example.com's actual pattern — a generic title, an
# empty app div, no real posting content anywhere in the static HTML.
_SPA_SHELL_HTML = """
<html>
  <head><title>JPMC Candidate Experience page</title></head>
  <body><div id="app"></div></body>
</html>
"""

_RENDERED_WITH_MAIN_HTML = f"""
<html>
  <head><title>Backend Engineer at Acme</title></head>
  <body>
    <nav>Home | Jobs | About</nav>
    <main><h1>Backend Engineer</h1><p>{"We build distributed systems that scale. " * 10}</p></main>
    <footer>Copyright Acme</footer>
  </body>
</html>
"""

_RENDERED_NO_MAIN_HTML = f"""
<html>
  <head><title>Backend Engineer at Acme</title></head>
  <body><h1>Backend Engineer</h1><p>{"We build distributed systems that scale. " * 10}</p></body>
</html>
"""


def test_is_meaningful_content_matches_the_spa_shell_example():
    # The exact motivating example from the task brief.
    assert is_meaningful_content("JPMC Candidate Experience page", "JPMC Candidate Experience page") is False
    assert is_meaningful_content(("We build distributed systems that scale. " * 10), "Backend Engineer") is True


@pytest.mark.asyncio
async def test_a_meaningful_static_content_never_calls_fallback():
    static = _FakeRenderer(html=_MEANINGFUL_HTML, name="static")
    fallback = _FakeRenderer(html=_RENDERED_WITH_MAIN_HTML, name="browser")
    client = PageFetchClient(static, FAST_CONFIG, fallback_renderer=fallback)

    page = await client.fetch("https://jobs.example.com/real-posting")

    assert "distributed systems" in page.text
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_b_spa_shell_static_content_triggers_fallback():
    static = _FakeRenderer(html=_SPA_SHELL_HTML, name="static")
    fallback = _FakeRenderer(html=_RENDERED_WITH_MAIN_HTML, name="browser")
    client = PageFetchClient(static, FAST_CONFIG, fallback_renderer=fallback)

    await client.fetch("https://jobs.example.com/spa-shell")

    assert fallback.calls == ["https://jobs.example.com/spa-shell"]


@pytest.mark.asyncio
async def test_b_static_error_also_triggers_fallback():
    static = _FakeRenderer(error=RuntimeError("connection reset"), name="static")
    fallback = _FakeRenderer(html=_RENDERED_WITH_MAIN_HTML, name="browser")
    client = PageFetchClient(static, FAST_CONFIG, fallback_renderer=fallback)

    page = await client.fetch("https://jobs.example.com/static-down")

    assert fallback.calls == ["https://jobs.example.com/static-down"]
    assert "distributed systems" in page.text


@pytest.mark.asyncio
async def test_c_rendered_main_text_is_returned():
    static = _FakeRenderer(html=_SPA_SHELL_HTML, name="static")
    fallback = _FakeRenderer(html=_RENDERED_WITH_MAIN_HTML, name="browser")
    client = PageFetchClient(static, FAST_CONFIG, fallback_renderer=fallback)

    page = await client.fetch("https://jobs.example.com/spa-shell")

    assert "Backend Engineer" in page.text
    assert "distributed systems" in page.text
    # nav/footer chrome outside <main> must not leak into the result.
    assert "Home | Jobs | About" not in page.text
    assert "Copyright Acme" not in page.text


@pytest.mark.asyncio
async def test_d_no_main_element_falls_back_to_body_text():
    static = _FakeRenderer(html=_SPA_SHELL_HTML, name="static")
    fallback = _FakeRenderer(html=_RENDERED_NO_MAIN_HTML, name="browser")
    client = PageFetchClient(static, FAST_CONFIG, fallback_renderer=fallback)

    page = await client.fetch("https://jobs.example.com/spa-shell-no-main")

    assert "Backend Engineer" in page.text
    assert "distributed systems" in page.text


@pytest.mark.asyncio
async def test_e_static_insufficient_and_browser_failure_raises_page_fetch_error():
    static = _FakeRenderer(html=_SPA_SHELL_HTML, name="static")
    fallback = _FakeRenderer(error=RuntimeError("chromium crashed"), name="browser")
    client = PageFetchClient(static, FAST_CONFIG, fallback_renderer=fallback)

    with pytest.raises(PageFetchError) as exc_info:
        await client.fetch("https://jobs.example.com/both-fail")

    message = str(exc_info.value)
    assert "insufficient" in message
    assert "browser fallback" in message
    assert "browser" in message  # renderer name present, per task brief


@pytest.mark.asyncio
async def test_e_static_error_and_browser_failure_raises_page_fetch_error():
    static = _FakeRenderer(error=RuntimeError("dns failure"), name="static")
    fallback = _FakeRenderer(error=RuntimeError("chromium crashed"), name="browser")
    client = PageFetchClient(static, FAST_CONFIG, fallback_renderer=fallback)

    with pytest.raises(PageFetchError) as exc_info:
        await client.fetch("https://jobs.example.com/both-fail-2")

    assert "browser fallback" in str(exc_info.value)


@pytest.mark.asyncio
async def test_f_browser_fallback_preserves_url_title_fetched_at_contract():
    static = _FakeRenderer(html=_SPA_SHELL_HTML, name="static")
    fallback = _FakeRenderer(html=_RENDERED_WITH_MAIN_HTML, name="browser")
    client = PageFetchClient(static, FAST_CONFIG, fallback_renderer=fallback)

    page = await client.fetch("https://jobs.example.com/spa-shell")

    assert isinstance(page, FetchedPage)
    assert page.url == "https://jobs.example.com/spa-shell"
    assert page.title == "Backend Engineer at Acme"
    assert page.fetched_at is not None
    assert page.html == _RENDERED_WITH_MAIN_HTML


@pytest.mark.asyncio
async def test_static_http_renderer_returns_response_text():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/job/1"
        return httpx.Response(200, text="<html><body>hi</body></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        renderer = StaticHttpRenderer(client=http)
        html = await renderer.render("https://jobs.example.com/job/1", timeout_seconds=5.0)

    assert html == "<html><body>hi</body></html>"


@pytest.mark.asyncio
async def test_static_http_renderer_sends_user_agent_when_configured():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["user_agent"] = request.headers.get("user-agent")
        return httpx.Response(200, text="ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        renderer = StaticHttpRenderer(user_agent="JobHuntBot/1.0", client=http)
        await renderer.render("https://jobs.example.com/job/1", timeout_seconds=5.0)

    assert captured["user_agent"] == "JobHuntBot/1.0"


@pytest.mark.asyncio
async def test_static_http_renderer_raises_on_http_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        renderer = StaticHttpRenderer(client=http)
        with pytest.raises(httpx.HTTPStatusError):
            await renderer.render("https://jobs.example.com/missing", timeout_seconds=5.0)


@pytest.mark.asyncio
async def test_no_fallback_configured_behaves_as_single_renderer_even_if_insufficient():
    # fallback_renderer defaults to None — the pre-existing single-renderer
    # contract every other test in this file exercises. Insufficient
    # content is returned as-is rather than raising, since there is nothing
    # to fall back to.
    static = _FakeRenderer(html=_SPA_SHELL_HTML, name="static")
    client = PageFetchClient(static, FAST_CONFIG)

    page = await client.fetch("https://jobs.example.com/spa-shell")

    assert "JPMC Candidate Experience page" in page.text
