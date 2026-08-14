"""Business logic for Job Ingestion Service's manual URL path.

Orchestrates `extraction.py` (fetch + LLM extraction), `repository.py`
(persist) and `events.py` (publish) to satisfy `POST /jobs/ingest-url` per
docs/architecture/component-contracts.md#job-ingestion-service.

Contract note (flagged, not silently resolved): component-contracts.md
describes an async path where the API returns `processing_status=PENDING`
immediately and a background step later updates the row to `NORMALIZED` or
`FAILED`. That would require Job Ingestion Service to UPDATE
`jobs.processing_status`, but database-ownership.md#jobs reserves that
column's update right to Job Matching Service only, and no background task
queue is defined anywhere in the architecture for this service. `Job`'s
`description_raw` is also a required field, so a `FAILED` row could not be
constructed before extraction has produced a description anyway. This
implementation therefore always completes extraction synchronously within
the request and only inserts a row (once, with its final status) on
success; on failure nothing is persisted and the caller receives the error
directly. See the job-agent's final report for the full writeup of this
gap.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

from jobs.ingestion.errors import JobIngestionError
from jobs.ingestion.extraction import (
    PageFetcher,
    StructuredExtractor,
    extract_job_from_url,
)
from jobs.repository import JobRepository
from shared.errors.codes import ErrorCode
from shared.types.domain.job import Job
from shared.types.dto import NormalizedJob
from shared.types.enums import JobProcessingStatus, JobSourceType
from shared.types.ids import JobId, UserId

_ALLOWED_SCHEMES = ("http", "https")

PublishFn = Callable[[NormalizedJob], object]
"""Signature `jobs.ingestion.events.publish_job_discovered` satisfies once
bound to a producer; kept as a plain callable here so tests can inject a
trivial fake without constructing a real `EventProducer`.
"""


def validate_url(url: str) -> str:
    """Raise `JobIngestionError(INVALID_JOB_URL)` unless `url` is a
    well-formed http(s) URL. Returns the trimmed URL otherwise.
    """
    candidate = url.strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise JobIngestionError(
            ErrorCode.INVALID_JOB_URL,
            f"url must be an http(s) URL with a host, got {url!r}",
        )
    return candidate


def canonicalize_url(url: str) -> str:
    """Normalize a job URL for dedup comparisons.

    Lowercases scheme/host, drops a trailing slash from the path, and
    strips the fragment (fragments never distinguish two postings). Query
    strings are preserved as-is: some boards encode the posting id only in
    a query parameter, so dropping query entirely would risk conflating
    two different postings.
    """
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/") or ""
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, "")
    )


def _to_normalized_job(job: Job) -> NormalizedJob:
    """`JobRecord` -> `NormalizedJob`, the one-way projection at publish
    time (shared-types.md#transformation-rules) — drops `processing_status`.
    """
    return NormalizedJob(
        job_id=job.id,
        user_id=job.user_id,
        company=job.company,
        title=job.title,
        location=job.location,
        description=job.description_raw,
        extracted_skills=job.extracted_skills,
        experience_required=job.experience_required,
        source_type=job.source_type,
        source_url=job.source_url,
        discovered_at=job.discovered_at,
    )


async def ingest_job_url(
    *,
    user_id: UserId,
    url: str,
    repository: JobRepository,
    page_fetcher: PageFetcher,
    extractor: StructuredExtractor,
    publish: PublishFn,
) -> Job:
    """Full manual-ingestion flow: validate, dedupe, fetch+extract, persist,
    publish.

    Returns the existing `Job` unchanged (no new row, no new event) if this
    user already ingested the same canonical URL — this is the manual-path
    half of dedup (docs/architecture/job-agent task 5).

    Raises `JobIngestionError` with `VALIDATION_ERROR`, `INVALID_JOB_URL`,
    `JOB_FETCH_FAILED`, or `LLM_PROVIDER_ERROR`.
    """
    if not str(url or "").strip():
        raise JobIngestionError(ErrorCode.VALIDATION_ERROR, "url must not be empty")

    validated_url = validate_url(url)
    canonical_url = canonicalize_url(validated_url)

    existing = await repository.find_by_source_url(user_id, canonical_url)
    if existing is not None:
        return existing

    fields = await extract_job_from_url(
        validated_url, page_fetcher=page_fetcher, extractor=extractor
    )

    job = Job(
        id=JobId(uuid4()),
        user_id=user_id,
        source_type=JobSourceType.MANUAL_URL,
        source_id=None,
        source_url=canonical_url,
        company=fields.company,
        title=fields.title,
        location=fields.location,
        description_raw=fields.description,
        extracted_skills=fields.extracted_skills,
        experience_required=fields.experience_required,
        processing_status=JobProcessingStatus.NORMALIZED,
        discovered_at=datetime.now(UTC),
    )
    await repository.insert_manual(job)

    publish(_to_normalized_job(job))

    return job


__all__ = [
    "PublishFn",
    "canonicalize_url",
    "ingest_job_url",
    "validate_url",
]
