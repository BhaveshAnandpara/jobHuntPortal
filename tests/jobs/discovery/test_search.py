"""Tests for `jobs.discovery.search.discover_jobs_for_source` — coarse
query building + per-hit fetch/extract normalization, no live network/LLM.
"""

from uuid import uuid4

import pytest

from infrastructure.external.job_search import JobSearchHit
from jobs.discovery.search import discover_jobs_for_source
from shared.types.enums import JobSourceType, RemoteWorkPreference
from shared.types.ids import UserId
from tests.jobs.conftest import (
    FakeExtractor,
    FakePageFetcher,
    FakeSearchClient,
    make_extracted_fields,
    make_job_source,
    make_resume_profile,
    make_user_preferences,
)

HIT_URL_A = "https://boards.example.com/jobs/1"
HIT_URL_B = "https://boards.example.com/jobs/2"


def _hit(url: str, **overrides: object) -> JobSearchHit:
    fields = {
        "source_url": url,
        "source_type": JobSourceType.LINKEDIN,
        "provider": "fake-board",
    }
    fields.update(overrides)
    return JobSearchHit(**fields)


@pytest.mark.asyncio
async def test_disabled_source_returns_no_results() -> None:
    user_id = UserId(uuid4())
    source = make_job_source(user_id, enabled=False)
    search_client = FakeSearchClient(hits=[_hit(HIT_URL_A)])

    results = await discover_jobs_for_source(
        source,
        preferences=None,
        profiles=[],
        search_client=search_client,
        page_fetcher=FakePageFetcher(),
        extractor=FakeExtractor(),
    )

    assert results == []
    assert search_client.queries == []  # never even searched


@pytest.mark.asyncio
async def test_normalizes_each_hit_via_full_page_extraction() -> None:
    user_id = UserId(uuid4())
    source = make_job_source(user_id)
    search_client = FakeSearchClient(hits=[_hit(HIT_URL_A), _hit(HIT_URL_B)])
    page_fetcher = FakePageFetcher(
        pages={HIT_URL_A: "content A", HIT_URL_B: "content B"}
    )
    extractor = FakeExtractor(
        by_content={
            "content A": make_extracted_fields(company="Company A", title="Role A"),
            "content B": make_extracted_fields(company="Company B", title="Role B"),
        }
    )

    results = await discover_jobs_for_source(
        source,
        preferences=None,
        profiles=[],
        search_client=search_client,
        page_fetcher=page_fetcher,
        extractor=extractor,
    )

    assert {job.company for job in results} == {"Company A", "Company B"}
    assert all(job.user_id == user_id for job in results)
    assert all(job.source_type is JobSourceType.LINKEDIN for job in results)


@pytest.mark.asyncio
async def test_per_hit_extraction_failure_is_skipped_not_raised() -> None:
    user_id = UserId(uuid4())
    source = make_job_source(user_id)
    search_client = FakeSearchClient(hits=[_hit(HIT_URL_A), _hit(HIT_URL_B)])
    page_fetcher = FakePageFetcher(
        pages={HIT_URL_B: "content B"}, errors={HIT_URL_A: RuntimeError("fetch failed")}
    )
    extractor = FakeExtractor(
        by_content={"content B": make_extracted_fields(company="Company B")}
    )

    results = await discover_jobs_for_source(
        source,
        preferences=None,
        profiles=[],
        search_client=search_client,
        page_fetcher=page_fetcher,
        extractor=extractor,
    )

    assert len(results) == 1
    assert results[0].company == "Company B"


@pytest.mark.asyncio
async def test_query_built_from_preferences_and_profiles() -> None:
    user_id = UserId(uuid4())
    source = make_job_source(user_id, query_config={"keywords": ["Robotics"]})
    preferences = make_user_preferences(
        user_id,
        target_roles=["Mechanical Engineer"],
        target_locations=["Berlin"],
        remote_preference=RemoteWorkPreference.HYBRID,
    )
    profiles = [make_resume_profile(user_id, target_roles=["Design Engineer"], skills=["CAD"])]
    search_client = FakeSearchClient(hits=[])

    await discover_jobs_for_source(
        source,
        preferences=preferences,
        profiles=profiles,
        search_client=search_client,
        page_fetcher=FakePageFetcher(),
        extractor=FakeExtractor(),
    )

    assert len(search_client.queries) == 1
    query = search_client.queries[0]
    assert "Robotics" in query.keywords
    assert "Mechanical Engineer" in query.keywords
    assert "Design Engineer" in query.keywords
    assert "CAD" in query.keywords
    assert query.location == "Berlin"
    assert query.remote_preference is RemoteWorkPreference.HYBRID


@pytest.mark.asyncio
async def test_query_keywords_are_deduplicated_case_insensitively() -> None:
    user_id = UserId(uuid4())
    source = make_job_source(user_id, query_config={"keywords": ["Mechanical Engineer"]})
    preferences = make_user_preferences(user_id, target_roles=["mechanical engineer"])
    search_client = FakeSearchClient(hits=[])

    await discover_jobs_for_source(
        source,
        preferences=preferences,
        profiles=[],
        search_client=search_client,
        page_fetcher=FakePageFetcher(),
        extractor=FakeExtractor(),
    )

    keywords = search_client.queries[0].keywords
    assert keywords.count("Mechanical Engineer") == 1


@pytest.mark.asyncio
async def test_missing_preferences_does_not_prevent_search() -> None:
    """No UserPreferences configured yet (User Service 404) — the source
    still searches using only its own query_config + any profiles.
    """
    user_id = UserId(uuid4())
    source = make_job_source(user_id, query_config={"keywords": ["Robotics"]})
    search_client = FakeSearchClient(hits=[])

    results = await discover_jobs_for_source(
        source,
        preferences=None,
        profiles=[],
        search_client=search_client,
        page_fetcher=FakePageFetcher(),
        extractor=FakeExtractor(),
    )

    assert results == []
    assert search_client.queries[0].keywords == ["Robotics"]
    assert search_client.queries[0].remote_preference is None
