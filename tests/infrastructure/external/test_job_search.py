"""Tests for infrastructure.external.job_search.JobBoardSearchClient.

Mocks at the JobBoardSearchProvider protocol boundary. Verifies the client
returns raw hits unfiltered/unranked (no relevance decision made here — that
stays with Job Discovery Service), and normalizes provider failures.
"""

import pytest

from infrastructure.external.config import ExternalClientConfig, RetryPolicy
from infrastructure.external.errors import JobSearchRequestError
from infrastructure.external.job_search import (
    JobBoardSearchClient,
    JobSearchHit,
    JobSearchQuery,
    StaticJobBoardSearchProvider,
)
from shared.types.enums import JobSourceType

FAST_CONFIG = ExternalClientConfig(
    retry=RetryPolicy(max_attempts=2, initial_backoff_seconds=0.001, backoff_multiplier=1.0)
)


def _hit(**overrides) -> JobSearchHit:
    defaults = {
        "source_url": "https://jobs.example.com/1",
        "source_type": JobSourceType.LINKEDIN,
        "provider": "static",
        "title": "Backend Engineer",
        "company": "Acme",
        "location": "Remote",
        "snippet": "Build things.",
    }
    defaults.update(overrides)
    return JobSearchHit(**defaults)


@pytest.mark.asyncio
async def test_search_returns_raw_hits_unfiltered():
    hits = [_hit(source_url="https://jobs.example.com/1"), _hit(source_url="https://jobs.example.com/2")]
    provider = StaticJobBoardSearchProvider(hits)
    client = JobBoardSearchClient(provider, FAST_CONFIG)

    result = await client.search(JobSearchQuery(keywords=["backend"]))

    assert result == hits


@pytest.mark.asyncio
async def test_search_respects_max_results():
    hits = [_hit(source_url=f"https://jobs.example.com/{i}") for i in range(5)]
    provider = StaticJobBoardSearchProvider(hits)
    client = JobBoardSearchClient(provider, FAST_CONFIG)

    result = await client.search(JobSearchQuery(max_results=2))

    assert len(result) == 2


@pytest.mark.asyncio
async def test_search_empty_results():
    provider = StaticJobBoardSearchProvider([])
    client = JobBoardSearchClient(provider, FAST_CONFIG)

    result = await client.search(JobSearchQuery(keywords=["nonexistent role"]))

    assert result == []


@pytest.mark.asyncio
async def test_search_does_not_filter_by_keyword_relevance():
    # This layer performs the query handed to it and returns whatever the
    # provider gives back — it must not itself drop hits based on keyword
    # match; that judgment belongs to Job Matching Service downstream.
    hits = [_hit(title="Completely unrelated posting")]
    provider = StaticJobBoardSearchProvider(hits)
    client = JobBoardSearchClient(provider, FAST_CONFIG)

    result = await client.search(JobSearchQuery(keywords=["mechanical engineer"]))

    assert result == hits


@pytest.mark.asyncio
async def test_search_normalizes_provider_failure():
    class _FailingProvider:
        name = "flaky-board"

        async def search(self, query: JobSearchQuery, *, timeout_seconds: float):
            raise ConnectionError("board unreachable")

    client = JobBoardSearchClient(_FailingProvider(), FAST_CONFIG)

    with pytest.raises(JobSearchRequestError) as exc_info:
        await client.search(JobSearchQuery(keywords=["anything"]))

    assert exc_info.value.provider == "flaky-board"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_search_retries_then_succeeds():
    call_count = {"n": 0}
    hits = [_hit()]

    class _FlakyProvider:
        name = "flaky"

        async def search(self, query: JobSearchQuery, *, timeout_seconds: float):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise TimeoutError("slow board")
            return hits

    client = JobBoardSearchClient(_FlakyProvider(), FAST_CONFIG)

    result = await client.search(JobSearchQuery())

    assert call_count["n"] == 2
    assert result == hits


@pytest.mark.asyncio
async def test_search_query_carries_no_profession_specific_structure():
    # keywords is free text so the same client serves any profession.
    query = JobSearchQuery(keywords=["mechanical design", "CAD", "SolidWorks"])
    provider = StaticJobBoardSearchProvider([_hit()])
    client = JobBoardSearchClient(provider, FAST_CONFIG)

    result = await client.search(query)

    assert result  # ran without error regardless of profession vocabulary
