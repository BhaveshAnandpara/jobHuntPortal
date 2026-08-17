"""Tests for infrastructure.external.people_search.PeopleSearchClient.

Mocks at the PeopleSearchProvider protocol boundary. Verifies raw,
unranked/unclassified hits are returned unchanged (classification and
ranking are Contact Discovery Service's job, not this layer's), and that
provider failures are normalized to CONTACT_SEARCH_FAILED.
"""

import httpx
import pytest

from infrastructure.external.config import ExternalClientConfig, RetryPolicy
from infrastructure.external.errors import PeopleSearchRequestError
from infrastructure.external.people_search import (
    PeopleSearchClient,
    PeopleSearchQuery,
    PersonSearchHit,
    PublicWebSearchProvider,
    SerpApiProvider,
    SerperProvider,
    StaticPeopleSearchProvider,
    default_people_search_provider,
)

FAST_CONFIG = ExternalClientConfig(
    retry=RetryPolicy(max_attempts=2, initial_backoff_seconds=0.001, backoff_multiplier=1.0)
)


def _hit(**overrides) -> PersonSearchHit:
    defaults = {
        "full_name": "Jamie Rivera",
        "provider": "static",
        "headline": "Senior Mechanical Engineer",
        "company": "Acme",
        "profile_url": "https://example.com/jamie",
        "email": None,
    }
    defaults.update(overrides)
    return PersonSearchHit(**defaults)


@pytest.mark.asyncio
async def test_search_returns_raw_hits_unranked():
    hits = [_hit(full_name="Jamie Rivera"), _hit(full_name="Alex Chen")]
    provider = StaticPeopleSearchProvider(hits)
    client = PeopleSearchClient(provider, FAST_CONFIG)

    result = await client.search(PeopleSearchQuery(company="Acme"))

    assert result == hits


@pytest.mark.asyncio
async def test_search_respects_max_results():
    hits = [_hit(full_name=f"Person {i}") for i in range(5)]
    provider = StaticPeopleSearchProvider(hits)
    client = PeopleSearchClient(provider, FAST_CONFIG)

    result = await client.search(PeopleSearchQuery(company="Acme", max_results=3))

    assert len(result) == 3


@pytest.mark.asyncio
async def test_search_empty_results():
    provider = StaticPeopleSearchProvider([])
    client = PeopleSearchClient(provider, FAST_CONFIG)

    result = await client.search(PeopleSearchQuery(company="Nobody Inc"))

    assert result == []


@pytest.mark.asyncio
async def test_search_role_keywords_are_free_text_any_profession():
    # role_keywords carries whatever the caller supplies for any profession;
    # this layer does not validate or restrict the vocabulary.
    query = PeopleSearchQuery(
        company="Acme", role_keywords=["HR Business Partner", "Talent Acquisition Lead"]
    )
    provider = StaticPeopleSearchProvider([_hit()])
    client = PeopleSearchClient(provider, FAST_CONFIG)

    result = await client.search(query)

    assert result  # ran without error regardless of profession vocabulary


@pytest.mark.asyncio
async def test_search_normalizes_provider_failure_to_contact_search_failed():
    class _FailingProvider:
        name = "flaky-people"

        async def search(self, query: PeopleSearchQuery, *, timeout_seconds: float):
            raise ConnectionError("provider unreachable")

    client = PeopleSearchClient(_FailingProvider(), FAST_CONFIG)

    with pytest.raises(PeopleSearchRequestError) as exc_info:
        await client.search(PeopleSearchQuery(company="Acme"))

    assert exc_info.value.provider == "flaky-people"


@pytest.mark.asyncio
async def test_search_retries_then_succeeds():
    call_count = {"n": 0}
    hits = [_hit()]

    class _FlakyProvider:
        name = "flaky"

        async def search(self, query: PeopleSearchQuery, *, timeout_seconds: float):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise TimeoutError("slow provider")
            return hits

    client = PeopleSearchClient(_FlakyProvider(), FAST_CONFIG)

    result = await client.search(PeopleSearchQuery(company="Acme"))

    assert call_count["n"] == 2
    assert result == hits


# ---------------------------------------------------------------------------
# PublicWebSearchProvider (real, network-backed Google CSE implementation)
# ---------------------------------------------------------------------------


def _cse_response(items: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"items": items} if items else {})


@pytest.mark.asyncio
async def test_public_web_search_provider_returns_normalized_hits():
    # A. provider returns normalized search results.
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == "test-key"
        assert request.url.params["cx"] == "test-engine"
        return _cse_response(
            [
                {
                    "title": "Rahul Sharma - Senior Software Engineer - JPMorgan Chase",
                    "link": "https://www.linkedin.com/in/rahul-sharma",
                    "snippet": "Senior Software Engineer at JPMorgan Chase",
                }
            ]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = PublicWebSearchProvider(api_key="test-key", engine_id="test-engine", client=http)
        hits = await provider.search(
            PeopleSearchQuery(company="JPMorgan Chase", role_keywords=["Software Engineer"]),
            timeout_seconds=5.0,
        )

    assert len(hits) == 1
    hit = hits[0]
    assert hit.full_name == "Rahul Sharma"
    assert hit.headline == "Senior Software Engineer"
    assert hit.company == "JPMorgan Chase"
    assert hit.profile_url == "https://www.linkedin.com/in/rahul-sharma"
    assert hit.provider == "public-web-search"


@pytest.mark.asyncio
async def test_public_web_search_provider_strips_linkedin_title_suffix():
    # B. provider returns LinkedIn/public-profile URL correctly, and a
    # trailing "| LinkedIn" title suffix does not leak into the parsed name.
    def handler(request: httpx.Request) -> httpx.Response:
        return _cse_response(
            [
                {
                    "title": "Alex Chen - HR Manager - Acme Corp | LinkedIn",
                    "link": "https://linkedin.com/in/alex-chen",
                    "snippet": "HR Manager at Acme Corp",
                }
            ]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = PublicWebSearchProvider(api_key="k", engine_id="e", client=http)
        hits = await provider.search(PeopleSearchQuery(company="Acme Corp"), timeout_seconds=5.0)

    assert hits[0].full_name == "Alex Chen"
    assert hits[0].headline == "HR Manager"
    assert hits[0].company == "Acme Corp"
    assert hits[0].profile_url == "https://linkedin.com/in/alex-chen"


@pytest.mark.asyncio
async def test_public_web_search_provider_empty_results_returns_empty_list():
    # C. empty results return [].
    def handler(request: httpx.Request) -> httpx.Response:
        return _cse_response([])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = PublicWebSearchProvider(api_key="k", engine_id="e", client=http)
        hits = await provider.search(PeopleSearchQuery(company="Nobody Inc"), timeout_seconds=5.0)

    assert hits == []


@pytest.mark.asyncio
async def test_public_web_search_provider_dedupes_same_profile_across_subqueries():
    # D. duplicate URLs are deduplicated — the same profile surfaces for
    # more than one role_keyword subquery and must only appear once.
    def handler(request: httpx.Request) -> httpx.Response:
        return _cse_response(
            [
                {
                    "title": "Jamie Rivera - Engineer - Acme",
                    "link": "https://www.linkedin.com/in/jamie-rivera/",
                    "snippet": "",
                }
            ]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = PublicWebSearchProvider(api_key="k", engine_id="e", client=http)
        hits = await provider.search(
            PeopleSearchQuery(company="Acme", role_keywords=["Engineer", "Software Engineer"]),
            timeout_seconds=5.0,
        )

    assert len(hits) == 1


@pytest.mark.asyncio
async def test_public_web_search_provider_does_not_guess_malformed_or_missing_fields():
    # E. malformed/incomplete result does not cause guessing: an item with
    # no title is skipped entirely; a title with no recognizable
    # "Name - Headline - Company" shape keeps the literal title as the name
    # and leaves company null rather than mis-splitting it.
    def handler(request: httpx.Request) -> httpx.Response:
        return _cse_response(
            [
                {
                    "title": "JPMorgan Chase Careers",
                    "link": "https://jpmorganchase.com/careers",
                    "snippet": "Join us",
                },
                {
                    "link": "https://linkedin.com/in/no-title",
                    "snippet": "no title at all",
                },
            ]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = PublicWebSearchProvider(api_key="k", engine_id="e", client=http)
        hits = await provider.search(PeopleSearchQuery(company="JPMorgan Chase"), timeout_seconds=5.0)

    assert len(hits) == 1  # the title-less item was skipped, not guessed
    assert hits[0].full_name == "JPMorgan Chase Careers"
    assert hits[0].headline == "Join us"  # falls back to snippet
    assert hits[0].company is None  # never guessed


@pytest.mark.asyncio
async def test_public_web_search_provider_failure_normalizes_to_contact_search_failed():
    # F. timeout/provider failure maps to the existing external error model,
    # exercised through PeopleSearchClient (the boundary Contact Discovery
    # actually calls).
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = PublicWebSearchProvider(api_key="k", engine_id="e", client=http)
        client = PeopleSearchClient(provider, FAST_CONFIG)

        with pytest.raises(PeopleSearchRequestError) as exc_info:
            await client.search(PeopleSearchQuery(company="Acme"))

    assert exc_info.value.provider == "public-web-search"


@pytest.mark.asyncio
async def test_public_web_search_provider_logs_request_and_response(caplog):
    # Verifies the query sent and the response received are both visible in
    # the log, so a live run's actual CSE query/result can be inspected
    # without a debugger — without leaking the api_key or full snippets.
    def handler(request: httpx.Request) -> httpx.Response:
        return _cse_response(
            [{"title": "Jamie Rivera - Engineer - Acme", "link": "https://linkedin.com/in/jamie-rivera", "snippet": ""}]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = PublicWebSearchProvider(api_key="super-secret-key", engine_id="test-engine", client=http)
        with caplog.at_level("INFO"):
            await provider.search(PeopleSearchQuery(company="Acme", role_keywords=["Engineer"]), timeout_seconds=5.0)

    request_lines = [r.message for r in caplog.records if "Google CSE request" in r.message]
    response_lines = [r.message for r in caplog.records if "Google CSE response" in r.message]
    assert request_lines and "site:linkedin.com/in" in request_lines[0] and '"Acme"' in request_lines[0]
    assert response_lines and "item_count=1" in response_lines[0]
    assert "super-secret-key" not in " ".join(request_lines + response_lines)


@pytest.mark.asyncio
async def test_public_web_search_provider_logs_response_on_http_error(caplog):
    # A non-2xx response (bad cx/key, quota exceeded, ...) must still be
    # visible in the log with its status/body before propagating — the
    # success-path "Google CSE response" log never fires in this case, so
    # without this, an HTTP error looks identical to a genuine zero-result
    # search in the log (both end at "no contacts found" downstream).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text='{"error": {"message": "API key not valid"}}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = PublicWebSearchProvider(api_key="k", engine_id="e", client=http)
        client = PeopleSearchClient(provider, FAST_CONFIG)
        with caplog.at_level("WARNING"):
            with pytest.raises(PeopleSearchRequestError):
                await client.search(PeopleSearchQuery(company="Acme"))

    error_lines = [r.message for r in caplog.records if "Google CSE response" in r.message]
    assert error_lines
    assert "status=403" in error_lines[0]
    assert "API key not valid" in error_lines[0]


@pytest.mark.asyncio
async def test_public_web_search_provider_rate_limit_retries_are_bounded():
    # G. rate-limit (429) responses are retried under the existing bounded
    # retry policy, never aggressively/infinitely.
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(429, json={"error": "rate limited"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = PublicWebSearchProvider(api_key="k", engine_id="e", client=http)
        client = PeopleSearchClient(provider, FAST_CONFIG)  # FAST_CONFIG: max_attempts=2

        with pytest.raises(PeopleSearchRequestError):
            await client.search(PeopleSearchQuery(company="Acme"))

    assert call_count["n"] == FAST_CONFIG.retry.max_attempts


@pytest.mark.asyncio
async def test_public_web_search_provider_reduces_multi_region_location_to_primary():
    # H. a multi-region location string (e.g. a remote-first job posting's
    # "United States & Canada, India, United Kingdom, Brazil, European
    # Union") must not be appended to the query in full — Google ANDs
    # every unquoted word together, so all ~9 words would need to appear
    # on one profile, which is effectively unsatisfiable and silently
    # zeroes out the search. Only the first region should end up in the
    # query string.
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["q"] = request.url.params["q"]
        return _cse_response([])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = PublicWebSearchProvider(api_key="k", engine_id="e", client=http)
        await provider.search(
            PeopleSearchQuery(
                company="Infisical",
                role_keywords=["Full Stack Engineer"],
                location="United States & Canada, India, United Kingdom, Brazil, European Union",
            ),
            timeout_seconds=5.0,
        )

    assert "United States & Canada" in captured["q"]
    assert "India" not in captured["q"]
    assert "Brazil" not in captured["q"]


@pytest.mark.asyncio
async def test_public_web_search_provider_single_location_passes_through():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["q"] = request.url.params["q"]
        return _cse_response([])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = PublicWebSearchProvider(api_key="k", engine_id="e", client=http)
        await provider.search(
            PeopleSearchQuery(company="Acme", location="Remote"), timeout_seconds=5.0
        )

    assert "Remote" in captured["q"]


# ---------------------------------------------------------------------------
# SerpApiProvider (real, network-backed serpapi.com implementation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serpapi_provider_returns_normalized_hits():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["api_key"] == "test-key"
        assert request.url.params["engine"] == "google"
        assert "site:linkedin.com/in" in request.url.params["q"]
        return httpx.Response(
            200,
            json={
                "organic_results": [
                    {
                        "title": "Rahul Sharma - Senior Software Engineer - JPMorgan Chase",
                        "link": "https://www.linkedin.com/in/rahul-sharma",
                        "snippet": "Senior Software Engineer at JPMorgan Chase",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = SerpApiProvider(api_key="test-key", client=http)
        hits = await provider.search(
            PeopleSearchQuery(company="JPMorgan Chase", role_keywords=["Software Engineer"]),
            timeout_seconds=5.0,
        )

    assert len(hits) == 1
    assert hits[0].full_name == "Rahul Sharma"
    assert hits[0].provider == "serpapi"


@pytest.mark.asyncio
async def test_serpapi_provider_empty_results_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = SerpApiProvider(api_key="k", client=http)
        hits = await provider.search(PeopleSearchQuery(company="Nobody Inc"), timeout_seconds=5.0)

    assert hits == []


@pytest.mark.asyncio
async def test_serpapi_provider_logs_request_and_response_without_leaking_key(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"organic_results": [{"title": "A - B - C", "link": "https://linkedin.com/in/a"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = SerpApiProvider(api_key="super-secret-key", client=http)
        with caplog.at_level("INFO"):
            await provider.search(PeopleSearchQuery(company="Acme"), timeout_seconds=5.0)

    lines = [r.message for r in caplog.records if "SerpApi" in r.message]
    assert any("SerpApi request" in line for line in lines)
    assert any("SerpApi response" in line and "item_count=1" in line for line in lines)
    assert "super-secret-key" not in " ".join(lines)


@pytest.mark.asyncio
async def test_serpapi_provider_logs_response_on_http_error(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='{"error": "Invalid API key"}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = SerpApiProvider(api_key="k", client=http)
        client = PeopleSearchClient(provider, FAST_CONFIG)
        with caplog.at_level("WARNING"):
            with pytest.raises(PeopleSearchRequestError):
                await client.search(PeopleSearchQuery(company="Acme"))

    error_lines = [r.message for r in caplog.records if "SerpApi response" in r.message]
    assert error_lines
    assert "status=401" in error_lines[0]
    assert "Invalid API key" in error_lines[0]


# ---------------------------------------------------------------------------
# SerperProvider (real, network-backed google.serper.dev implementation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serper_provider_returns_normalized_hits():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-KEY"] == "test-key"
        assert request.method == "POST"
        assert "site:linkedin.com/in" in request.content.decode()
        return httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "Priya Nair - Recruiter - Acme",
                        "link": "https://www.linkedin.com/in/priya-nair",
                        "snippet": "Recruiter at Acme",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = SerperProvider(api_key="test-key", client=http)
        hits = await provider.search(PeopleSearchQuery(company="Acme"), timeout_seconds=5.0)

    assert len(hits) == 1
    assert hits[0].full_name == "Priya Nair"
    assert hits[0].provider == "serper"


@pytest.mark.asyncio
async def test_serper_provider_empty_results_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = SerperProvider(api_key="k", client=http)
        hits = await provider.search(PeopleSearchQuery(company="Nobody Inc"), timeout_seconds=5.0)

    assert hits == []


@pytest.mark.asyncio
async def test_serper_provider_logs_response_on_http_error(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text='{"message": "Not enough credits"}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = SerperProvider(api_key="k", client=http)
        client = PeopleSearchClient(provider, FAST_CONFIG)
        with caplog.at_level("WARNING"):
            with pytest.raises(PeopleSearchRequestError):
                await client.search(PeopleSearchQuery(company="Acme"))

    error_lines = [r.message for r in caplog.records if "Serper response" in r.message]
    assert error_lines
    assert "status=403" in error_lines[0]
    assert "Not enough credits" in error_lines[0]


# ---------------------------------------------------------------------------
# default_people_search_provider (H: PeopleSearchClient uses the provider
# selected via configuration)
# ---------------------------------------------------------------------------


def test_default_provider_falls_back_to_static_when_unconfigured(monkeypatch):
    monkeypatch.delenv("PEOPLE_SEARCH_PROVIDER", raising=False)

    provider = default_people_search_provider()

    assert isinstance(provider, StaticPeopleSearchProvider)


def test_default_provider_falls_back_to_static_when_credentials_missing(monkeypatch):
    monkeypatch.setenv("PEOPLE_SEARCH_PROVIDER", "google-cse")
    monkeypatch.delenv("PEOPLE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("PEOPLE_SEARCH_ENGINE_ID", raising=False)

    provider = default_people_search_provider()

    assert isinstance(provider, StaticPeopleSearchProvider)


def test_default_provider_resolves_public_web_search_when_configured(monkeypatch):
    monkeypatch.setenv("PEOPLE_SEARCH_PROVIDER", "google-cse")
    monkeypatch.setenv("PEOPLE_SEARCH_API_KEY", "test-key")
    monkeypatch.setenv("PEOPLE_SEARCH_ENGINE_ID", "test-engine")

    provider = default_people_search_provider()

    assert isinstance(provider, PublicWebSearchProvider)
    assert provider.name == "public-web-search"


def test_default_provider_falls_back_to_static_when_serpapi_key_missing(monkeypatch):
    monkeypatch.setenv("PEOPLE_SEARCH_PROVIDER", "serpapi")
    monkeypatch.delenv("PEOPLE_SEARCH_API_KEY", raising=False)

    provider = default_people_search_provider()

    assert isinstance(provider, StaticPeopleSearchProvider)


def test_default_provider_resolves_serpapi_when_configured(monkeypatch):
    monkeypatch.setenv("PEOPLE_SEARCH_PROVIDER", "serpapi")
    monkeypatch.setenv("PEOPLE_SEARCH_API_KEY", "test-key")
    monkeypatch.delenv("PEOPLE_SEARCH_ENGINE_ID", raising=False)

    provider = default_people_search_provider()

    assert isinstance(provider, SerpApiProvider)
    assert provider.name == "serpapi"


def test_default_provider_falls_back_to_static_when_serper_key_missing(monkeypatch):
    monkeypatch.setenv("PEOPLE_SEARCH_PROVIDER", "serper")
    monkeypatch.delenv("PEOPLE_SEARCH_API_KEY", raising=False)

    provider = default_people_search_provider()

    assert isinstance(provider, StaticPeopleSearchProvider)


def test_default_provider_resolves_serper_when_configured(monkeypatch):
    monkeypatch.setenv("PEOPLE_SEARCH_PROVIDER", "serper")
    monkeypatch.setenv("PEOPLE_SEARCH_API_KEY", "test-key")

    provider = default_people_search_provider()

    assert isinstance(provider, SerperProvider)
    assert provider.name == "serper"


# ---------------------------------------------------------------------------
# I: Contact Discovery consumes provider output without any contract change
# ---------------------------------------------------------------------------


def test_public_web_search_provider_hits_flow_into_contact_candidates_unchanged():
    from shared.types.enums import ContactType
    from workflows.langgraph.contact_discovery.discovery import hits_to_candidates

    # Same PersonSearchHit shape PublicWebSearchProvider.search() returns —
    # proves Contact Discovery's existing hits_to_candidates needs no
    # contract change to consume real provider output.
    hit = PersonSearchHit(
        full_name="Priya Nair",
        provider="public-web-search",
        headline="Recruiter",
        company="Acme",
        profile_url="https://linkedin.com/in/priya-nair",
        email=None,
    )

    candidates = hits_to_candidates([hit], [ContactType.RECRUITER], default_company="Acme")

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.full_name == "Priya Nair"
    assert candidate.headline == "Recruiter"
    assert candidate.company == "Acme"
    assert candidate.profile_url == "https://linkedin.com/in/priya-nair"
    assert candidate.source == "public-web-search"
    assert candidate.contact_type == ContactType.RECRUITER


# ---------------------------------------------------------------------------
# J: hits_to_candidates' company_confirmed — a candidate must never look
# same-company just because the search happened to be scoped to that
# company; only real evidence (a parsed company field, or the company's
# own name in the candidate's own headline) counts.
# ---------------------------------------------------------------------------


def test_hits_to_candidates_confirms_company_when_provider_parsed_it():
    from shared.types.enums import ContactType
    from workflows.langgraph.contact_discovery.discovery import hits_to_candidates

    hit = PersonSearchHit(
        full_name="Priya Nair", provider="serper", headline="Recruiter", company="Acme"
    )

    [candidate] = hits_to_candidates([hit], [ContactType.RECRUITER], default_company="Acme")

    assert candidate.company_confirmed is True


def test_hits_to_candidates_confirms_company_mentioned_inline_in_headline():
    """Title didn't split into its own company segment (common — Google
    truncates/reformats titles unpredictably), but the target company's own
    name is right there in the candidate's headline text — real evidence,
    not a default."""
    from shared.types.enums import ContactType
    from workflows.langgraph.contact_discovery.discovery import hits_to_candidates

    hit = PersonSearchHit(
        full_name="Gaurav Pareek",
        provider="serpapi",
        headline="Software Engineer @Viamedia | Elixir",
        company=None,
    )

    [candidate] = hits_to_candidates([hit], [ContactType.PRACTITIONER], default_company="Viamedia")

    assert candidate.company_confirmed is True
    assert candidate.company == "Viamedia"


def test_hits_to_candidates_leaves_company_unconfirmed_with_no_evidence():
    """No parsed company field, and the target company's name never
    appears in the headline at all — the hit only surfaced because some
    other part of the indexed page matched the site-restricted search.
    Must not be silently treated as same-company."""
    from shared.types.enums import ContactType
    from workflows.langgraph.contact_discovery.discovery import hits_to_candidates

    hit = PersonSearchHit(
        full_name="Carol Jenkins",
        provider="serpapi",
        headline="Frontend Engineer / Web Developer",
        company=None,
    )

    [candidate] = hits_to_candidates([hit], [ContactType.PRACTITIONER], default_company="Viamedia")

    assert candidate.company_confirmed is False
    assert candidate.company == "Viamedia"  # still a display value, just not confirmed


def test_hits_to_candidates_headline_mention_requires_word_boundary():
    """"Via" must not confirm a match against a headline that merely
    contains "Reliable" or some other word starting with those letters —
    only a distinct word/phrase match counts."""
    from shared.types.enums import ContactType
    from workflows.langgraph.contact_discovery.discovery import hits_to_candidates

    hit = PersonSearchHit(
        full_name="Someone Else",
        provider="serpapi",
        headline="Reliable Backend Engineer",
        company=None,
    )

    [candidate] = hits_to_candidates([hit], [ContactType.PRACTITIONER], default_company="Via")

    assert candidate.company_confirmed is False
