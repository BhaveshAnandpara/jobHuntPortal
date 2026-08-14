"""Tests for infrastructure.external.people_search.PeopleSearchClient.

Mocks at the PeopleSearchProvider protocol boundary. Verifies raw,
unranked/unclassified hits are returned unchanged (classification and
ranking are Contact Discovery Service's job, not this layer's), and that
provider failures are normalized to CONTACT_SEARCH_FAILED.
"""

import pytest

from infrastructure.external.config import ExternalClientConfig, RetryPolicy
from infrastructure.external.errors import PeopleSearchRequestError
from infrastructure.external.people_search import (
    PeopleSearchClient,
    PeopleSearchQuery,
    PersonSearchHit,
    StaticPeopleSearchProvider,
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
