"""Tests for `jobs.discovery.service.run_discovery` — the Automatic
Discovery Run orchestration: per enabled JobSource, search+normalize,
dedupe, persist, publish, mark_run — with per-posting and per-source
failure isolation (component-contracts.md: "a single failure doesn't stop
the run").
"""

from uuid import uuid4

import pytest

from infrastructure.external.job_search import JobSearchHit
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.topics import Topic
from jobs.discovery.service import run_discovery
from shared.types.enums import JobSourceType
from shared.types.ids import UserId
from tests.jobs.conftest import (
    FakeExtractor,
    FakeJobRepository,
    FakeJobSourceRepository,
    FakePageFetcher,
    FakeProfileServiceClient,
    FakeSearchClient,
    FakeUserPreferencesClient,
    make_extracted_fields,
    make_job,
    make_job_source,
)

URL = "https://boards.example.com/jobs/1"


def _hit(url: str = URL) -> JobSearchHit:
    return JobSearchHit(source_url=url, source_type=JobSourceType.LINKEDIN, provider="fake")


def _producer(broker: InMemoryBroker | None = None) -> tuple[EventProducer, InMemoryBroker]:
    broker = broker if broker is not None else InMemoryBroker()
    return EventProducer("job-discovery-service", client=InMemoryProducerClient(broker)), broker


@pytest.mark.asyncio
async def test_run_discovery_persists_and_publishes_new_postings() -> None:
    user_id = UserId(uuid4())
    source = make_job_source(user_id)
    job_repository = FakeJobRepository()
    source_repository = FakeJobSourceRepository([source])
    producer, broker = _producer()

    persisted = await run_discovery(
        source_repository=source_repository,
        job_repository=job_repository,
        user_client=FakeUserPreferencesClient(),
        profile_client=FakeProfileServiceClient(),
        search_client=FakeSearchClient(hits=[_hit()]),
        page_fetcher=FakePageFetcher(pages={URL: "content"}),
        extractor=FakeExtractor(by_content={"content": make_extracted_fields()}),
        producer=producer,
    )

    assert len(persisted) == 1
    assert persisted[0].source_type is JobSourceType.LINKEDIN
    assert persisted[0].source_id == source.id
    assert job_repository.insert_discovered_calls == 1
    assert len(broker.log(Topic.JOBS_DISCOVERED.value)) == 1
    assert source_repository.mark_run_calls == [(source.id, source_repository.mark_run_calls[0][1])]


@pytest.mark.asyncio
async def test_run_discovery_dedupes_against_existing_job() -> None:
    user_id = UserId(uuid4())
    source = make_job_source(user_id)
    from jobs.ingestion.service import canonicalize_url

    existing = make_job(
        user_id,
        source_type=JobSourceType.LINKEDIN,
        source_id=source.id,
        source_url=canonicalize_url(URL),
    )
    job_repository = FakeJobRepository([existing])
    source_repository = FakeJobSourceRepository([source])
    producer, broker = _producer()

    persisted = await run_discovery(
        source_repository=source_repository,
        job_repository=job_repository,
        user_client=FakeUserPreferencesClient(),
        profile_client=FakeProfileServiceClient(),
        search_client=FakeSearchClient(hits=[_hit()]),
        page_fetcher=FakePageFetcher(pages={URL: "content"}),
        extractor=FakeExtractor(by_content={"content": make_extracted_fields()}),
        producer=producer,
    )

    assert persisted == []
    assert job_repository.insert_discovered_calls == 0
    assert broker.log(Topic.JOBS_DISCOVERED.value) == []
    # mark_run still happens — the source itself ran successfully.
    assert len(source_repository.mark_run_calls) == 1


@pytest.mark.asyncio
async def test_run_discovery_skips_disabled_sources() -> None:
    user_id = UserId(uuid4())
    enabled = make_job_source(user_id, enabled=True)
    disabled = make_job_source(user_id, enabled=False)
    job_repository = FakeJobRepository()
    source_repository = FakeJobSourceRepository([enabled, disabled])
    producer, _broker = _producer()

    await run_discovery(
        source_repository=source_repository,
        job_repository=job_repository,
        user_client=FakeUserPreferencesClient(),
        profile_client=FakeProfileServiceClient(),
        search_client=FakeSearchClient(hits=[]),
        page_fetcher=FakePageFetcher(),
        extractor=FakeExtractor(),
        producer=producer,
    )

    ran_source_ids = {call[0] for call in source_repository.mark_run_calls}
    assert ran_source_ids == {enabled.id}


@pytest.mark.asyncio
async def test_run_discovery_one_source_search_failure_does_not_abort_others() -> None:
    user_id = UserId(uuid4())
    failing = make_job_source(user_id, name="failing-source")
    healthy = make_job_source(user_id, name="healthy-source")
    job_repository = FakeJobRepository()
    source_repository = FakeJobSourceRepository([failing, healthy])
    producer, _broker = _producer()

    class SwitchingSearchClient:
        """Fails only for the first source it's asked to search."""

        def __init__(self) -> None:
            self.calls = 0

        async def search(self, query):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("job board is down")
            return [_hit()]

    persisted = await run_discovery(
        source_repository=source_repository,
        job_repository=job_repository,
        user_client=FakeUserPreferencesClient(),
        profile_client=FakeProfileServiceClient(),
        search_client=SwitchingSearchClient(),
        page_fetcher=FakePageFetcher(pages={URL: "content"}),
        extractor=FakeExtractor(by_content={"content": make_extracted_fields()}),
        producer=producer,
    )

    # Both sources still get their run recorded, and the healthy source's
    # posting is still persisted despite the other source's search failure.
    assert len(persisted) == 1
    assert {call[0] for call in source_repository.mark_run_calls} == {failing.id, healthy.id}


@pytest.mark.asyncio
async def test_run_discovery_per_posting_insert_failure_does_not_abort_source() -> None:
    user_id = UserId(uuid4())
    source = make_job_source(user_id)
    job_repository = FakeJobRepository()
    source_repository = FakeJobSourceRepository([source])
    producer, broker = _producer()

    url_a = "https://boards.example.com/jobs/a"
    url_b = "https://boards.example.com/jobs/b"
    hits = [
        JobSearchHit(source_url=url_a, source_type=JobSourceType.LINKEDIN, provider="fake"),
        JobSearchHit(source_url=url_b, source_type=JobSourceType.LINKEDIN, provider="fake"),
    ]

    original_insert = job_repository.insert_discovered

    async def flaky_insert(job):
        if job.company == "Broken Co":
            raise RuntimeError("db write failed")
        await original_insert(job)

    job_repository.insert_discovered = flaky_insert  # type: ignore[method-assign]

    persisted = await run_discovery(
        source_repository=source_repository,
        job_repository=job_repository,
        user_client=FakeUserPreferencesClient(),
        profile_client=FakeProfileServiceClient(),
        search_client=FakeSearchClient(hits=hits),
        page_fetcher=FakePageFetcher(pages={url_a: "content a", url_b: "content b"}),
        extractor=FakeExtractor(
            by_content={
                "content a": make_extracted_fields(company="Broken Co"),
                "content b": make_extracted_fields(company="Good Co"),
            }
        ),
        producer=producer,
    )

    assert len(persisted) == 1
    assert persisted[0].company == "Good Co"
    assert len(broker.log(Topic.JOBS_DISCOVERED.value)) == 1


@pytest.mark.asyncio
async def test_run_discovery_degrades_gracefully_when_preferences_and_profiles_fail() -> None:
    user_id = UserId(uuid4())
    source = make_job_source(user_id, query_config={"keywords": ["Robotics"]})
    job_repository = FakeJobRepository()
    source_repository = FakeJobSourceRepository([source])
    producer, _broker = _producer()

    persisted = await run_discovery(
        source_repository=source_repository,
        job_repository=job_repository,
        user_client=FakeUserPreferencesClient(error=RuntimeError("user service down")),
        profile_client=FakeProfileServiceClient(error=RuntimeError("profile service down")),
        search_client=FakeSearchClient(hits=[_hit()]),
        page_fetcher=FakePageFetcher(pages={URL: "content"}),
        extractor=FakeExtractor(by_content={"content": make_extracted_fields()}),
        producer=producer,
    )

    assert len(persisted) == 1
