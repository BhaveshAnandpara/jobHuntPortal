"""Tests for `jobs.ingestion.extraction.extract_job_from_url` — the fetch +
LLM extraction pipeline shared by the manual URL path (and, via
`jobs.discovery.search`, the automatic path). No live network or LLM.
"""

import pytest

from jobs.ingestion.errors import JobIngestionError
from jobs.ingestion.extraction import extract_job_from_url
from shared.errors.codes import ErrorCode
from tests.jobs.conftest import FakeExtractor, FakePageFetcher, make_extracted_fields

URL = "https://boards.example.com/jobs/42"


@pytest.mark.asyncio
async def test_extract_success_returns_fields() -> None:
    fetcher = FakePageFetcher(pages={URL: "full posting text"})
    extractor = FakeExtractor(
        by_content={"full posting text": make_extracted_fields(company="Acme")}
    )

    fields = await extract_job_from_url(URL, page_fetcher=fetcher, extractor=extractor)

    assert fields.company == "Acme"
    assert fetcher.requested == [URL]
    # The rendered prompt embeds the fetched page content verbatim.
    assert "full posting text" in extractor.prompts[0]


@pytest.mark.asyncio
async def test_extract_fetch_failure_raises_job_fetch_failed() -> None:
    fetcher = FakePageFetcher(errors={URL: RuntimeError("connection reset")})
    extractor = FakeExtractor(default=make_extracted_fields())

    with pytest.raises(JobIngestionError) as excinfo:
        await extract_job_from_url(URL, page_fetcher=fetcher, extractor=extractor)

    assert excinfo.value.error_code is ErrorCode.JOB_FETCH_FAILED


@pytest.mark.asyncio
async def test_extract_empty_page_raises_invalid_job_url() -> None:
    fetcher = FakePageFetcher(pages={URL: "   "})
    extractor = FakeExtractor(default=make_extracted_fields())

    with pytest.raises(JobIngestionError) as excinfo:
        await extract_job_from_url(URL, page_fetcher=fetcher, extractor=extractor)

    assert excinfo.value.error_code is ErrorCode.INVALID_JOB_URL


@pytest.mark.asyncio
async def test_extract_llm_failure_raises_llm_provider_error() -> None:
    fetcher = FakePageFetcher(pages={URL: "full posting text"})
    extractor = FakeExtractor(
        by_content={"full posting text": ValueError("model unavailable")}
    )

    with pytest.raises(JobIngestionError) as excinfo:
        await extract_job_from_url(URL, page_fetcher=fetcher, extractor=extractor)

    assert excinfo.value.error_code is ErrorCode.LLM_PROVIDER_ERROR


@pytest.mark.asyncio
async def test_extract_missing_company_and_title_raises_invalid_job_url() -> None:
    fetcher = FakePageFetcher(pages={URL: "not really a job posting"})
    extractor = FakeExtractor(
        by_content={
            "not really a job posting": make_extracted_fields(company="  ", title="  ")
        }
    )

    with pytest.raises(JobIngestionError) as excinfo:
        await extract_job_from_url(URL, page_fetcher=fetcher, extractor=extractor)

    assert excinfo.value.error_code is ErrorCode.INVALID_JOB_URL
