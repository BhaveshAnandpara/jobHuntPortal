"""Tests for `jobs.ingestion.service` — the manual-URL orchestration layer
(validate -> dedupe -> fetch+extract -> persist -> publish).
"""

from uuid import uuid4

import pytest

from jobs.ingestion.errors import JobIngestionError
from jobs.ingestion.service import canonicalize_url, ingest_job_url, validate_url
from shared.errors.codes import ErrorCode
from shared.types.enums import JobProcessingStatus, JobSourceType
from shared.types.ids import UserId
from tests.jobs.conftest import (
    FakeExtractor,
    FakeJobRepository,
    FakePageFetcher,
    RecordingPublisher,
    make_extracted_fields,
)

URL = "https://Boards.Example.com/jobs/42/?utm_source=x"


def _user() -> UserId:
    return UserId(uuid4())


# ---------------------------------------------------------------------------
# canonicalize_url / validate_url
# ---------------------------------------------------------------------------


def test_canonicalize_url_lowercases_scheme_and_host() -> None:
    assert canonicalize_url("HTTPS://Example.COM/Jobs/1") == "https://example.com/Jobs/1"


def test_canonicalize_url_strips_trailing_slash() -> None:
    assert canonicalize_url("https://example.com/jobs/1/") == "https://example.com/jobs/1"


def test_canonicalize_url_drops_fragment() -> None:
    assert canonicalize_url("https://example.com/jobs/1#apply") == "https://example.com/jobs/1"


def test_canonicalize_url_preserves_query() -> None:
    assert (
        canonicalize_url("https://example.com/jobs/1?id=42")
        == "https://example.com/jobs/1?id=42"
    )


def test_canonicalize_url_is_idempotent() -> None:
    once = canonicalize_url(URL)
    assert canonicalize_url(once) == once


def test_validate_url_rejects_non_http_scheme() -> None:
    with pytest.raises(JobIngestionError) as excinfo:
        validate_url("ftp://example.com/jobs/1")
    assert excinfo.value.error_code is ErrorCode.INVALID_JOB_URL


def test_validate_url_rejects_missing_host() -> None:
    with pytest.raises(JobIngestionError):
        validate_url("https:///jobs/1")


def test_validate_url_accepts_http_and_https() -> None:
    assert validate_url("http://example.com/1") == "http://example.com/1"
    assert validate_url("https://example.com/1") == "https://example.com/1"


# ---------------------------------------------------------------------------
# ingest_job_url
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_job_url_success_inserts_and_publishes() -> None:
    user_id = _user()
    repository = FakeJobRepository()
    fetcher = FakePageFetcher(pages={URL: "posting body"})
    extractor = FakeExtractor(
        by_content={"posting body": make_extracted_fields(company="Acme Robotics")}
    )
    publisher = RecordingPublisher()

    job = await ingest_job_url(
        user_id=user_id,
        url=URL,
        repository=repository,
        page_fetcher=fetcher,
        extractor=extractor,
        publish=publisher,
    )

    assert job.company == "Acme Robotics"
    assert job.source_type is JobSourceType.MANUAL_URL
    assert job.source_url == canonicalize_url(URL)
    assert job.processing_status is JobProcessingStatus.NORMALIZED
    assert repository.insert_manual_calls == 1
    assert len(publisher.published) == 1
    assert publisher.published[0].job_id == job.id
    assert publisher.published[0].source_type is JobSourceType.MANUAL_URL


@pytest.mark.asyncio
async def test_ingest_job_url_dedup_skips_insert_and_publish() -> None:
    user_id = _user()
    repository = FakeJobRepository()
    fetcher = FakePageFetcher(pages={URL: "posting body"})
    extractor = FakeExtractor(by_content={"posting body": make_extracted_fields()})
    publisher = RecordingPublisher()

    first = await ingest_job_url(
        user_id=user_id,
        url=URL,
        repository=repository,
        page_fetcher=fetcher,
        extractor=extractor,
        publish=publisher,
    )
    second = await ingest_job_url(
        user_id=user_id,
        url=URL,
        repository=repository,
        page_fetcher=fetcher,
        extractor=extractor,
        publish=publisher,
    )

    assert first.id == second.id
    assert repository.insert_manual_calls == 1
    assert len(publisher.published) == 1
    # Extraction only ran once — the second call short-circuited on dedup.
    assert fetcher.requested == [URL]


@pytest.mark.asyncio
async def test_ingest_job_url_dedup_is_per_user() -> None:
    """Two different users submitting the identical URL both get their own
    job row — dedup is keyed on (user_id, source_url), not source_url alone.
    """
    repository = FakeJobRepository()
    fetcher = FakePageFetcher(pages={URL: "posting body"})
    extractor = FakeExtractor(by_content={"posting body": make_extracted_fields()})
    publisher = RecordingPublisher()

    first = await ingest_job_url(
        user_id=_user(),
        url=URL,
        repository=repository,
        page_fetcher=fetcher,
        extractor=extractor,
        publish=publisher,
    )
    second = await ingest_job_url(
        user_id=_user(),
        url=URL,
        repository=repository,
        page_fetcher=fetcher,
        extractor=extractor,
        publish=publisher,
    )

    assert first.id != second.id
    assert repository.insert_manual_calls == 2
    assert len(publisher.published) == 2


@pytest.mark.asyncio
async def test_ingest_job_url_empty_url_raises_validation_error() -> None:
    repository = FakeJobRepository()
    with pytest.raises(JobIngestionError) as excinfo:
        await ingest_job_url(
            user_id=_user(),
            url="   ",
            repository=repository,
            page_fetcher=FakePageFetcher(),
            extractor=FakeExtractor(),
            publish=RecordingPublisher(),
        )
    assert excinfo.value.error_code is ErrorCode.VALIDATION_ERROR
    assert repository.jobs == []


@pytest.mark.asyncio
async def test_ingest_job_url_invalid_url_raises_before_fetch() -> None:
    fetcher = FakePageFetcher()
    with pytest.raises(JobIngestionError) as excinfo:
        await ingest_job_url(
            user_id=_user(),
            url="not-a-url",
            repository=FakeJobRepository(),
            page_fetcher=fetcher,
            extractor=FakeExtractor(),
            publish=RecordingPublisher(),
        )
    assert excinfo.value.error_code is ErrorCode.INVALID_JOB_URL
    assert fetcher.requested == []


@pytest.mark.asyncio
async def test_ingest_job_url_fetch_failure_inserts_nothing() -> None:
    repository = FakeJobRepository()
    fetcher = FakePageFetcher(errors={URL: RuntimeError("boom")})
    publisher = RecordingPublisher()

    with pytest.raises(JobIngestionError) as excinfo:
        await ingest_job_url(
            user_id=_user(),
            url=URL,
            repository=repository,
            page_fetcher=fetcher,
            extractor=FakeExtractor(default=make_extracted_fields()),
            publish=publisher,
        )

    assert excinfo.value.error_code is ErrorCode.JOB_FETCH_FAILED
    assert repository.jobs == []
    assert publisher.published == []
