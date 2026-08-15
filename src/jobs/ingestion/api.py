"""Job Ingestion Service API router.

Owned endpoints (docs/architecture/api-contracts.md#job-ingestion-service):
    POST /jobs/ingest-url
    GET  /jobs/{job_id}

Mounted into the app in api/main.py. `GET /jobs/{job_id}` was added during
Wave 3 (Outreach Service implementation) to close a real, flagged gap: it
was documented in api-contracts.md/component-contracts.md since the Job
Matching Service cleanup, but had never actually been wired — Outreach
Service needs it (via `outreach.clients.JobIngestionClient`) to get a job's
real `company`/`title` for message personalization, which no event payload
it consumes carries (see dependency-graph.md's newly-added
`Outreach Service ──► Job Ingestion Service` edge).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from infrastructure.auth import CurrentUserIdDependency
from infrastructure.kafka.producer import EventProducer
from infrastructure.logging import format_context, get_logger
from jobs.ingestion.dependencies import (
    get_event_producer,
    get_job_repository,
    get_page_fetcher,
    get_structured_extractor,
)
from jobs.ingestion.errors import JobIngestionError
from jobs.ingestion.events import publish_job_discovered
from jobs.ingestion.extraction import PageFetcher, StructuredExtractor
from jobs.ingestion.service import ingest_job_url
from jobs.repository import JobRepository
from shared.errors.codes import ErrorCode
from shared.types.api.jobs import IngestJobUrlRequest, JobResponse
from shared.types.domain.job import Job
from shared.types.ids import JobId

router = APIRouter(tags=["job-ingestion"])
logger = get_logger(__name__)

JobRepositoryDependency = Annotated[JobRepository, Depends(get_job_repository)]
PageFetcherDependency = Annotated[PageFetcher, Depends(get_page_fetcher)]
StructuredExtractorDependency = Annotated[StructuredExtractor, Depends(get_structured_extractor)]
EventProducerDependency = Annotated[EventProducer, Depends(get_event_producer)]

# api-contracts.md#post-jobsingest-url declares only 202/400/404 as valid
# status codes for this endpoint; every ingestion-time error surfaces as
# 400 except NOT_FOUND, which this service cannot itself detect (see the
# route handler's docstring for why user_id existence isn't validated here).
_STATUS_BY_ERROR_CODE = {
    ErrorCode.VALIDATION_ERROR: status.HTTP_400_BAD_REQUEST,
    ErrorCode.INVALID_JOB_URL: status.HTTP_400_BAD_REQUEST,
    ErrorCode.JOB_FETCH_FAILED: status.HTTP_400_BAD_REQUEST,
    ErrorCode.LLM_PROVIDER_ERROR: status.HTTP_400_BAD_REQUEST,
}


def _http_error(error: JobIngestionError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_BY_ERROR_CODE.get(error.error_code, status.HTTP_400_BAD_REQUEST),
        detail={"code": error.error_code.value, "message": error.message},
    )


def _job_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        user_id=job.user_id,
        company=job.company,
        title=job.title,
        location=job.location,
        description=job.description_raw,
        extracted_skills=job.extracted_skills,
        experience_required=job.experience_required,
        source_url=job.source_url,
        processing_status=job.processing_status,
        discovered_at=job.discovered_at,
    )


@router.post(
    "/jobs/ingest-url",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_job_url_route(
    request: IngestJobUrlRequest,
    user_id: CurrentUserIdDependency,
    repository: JobRepositoryDependency,
    page_fetcher: PageFetcherDependency,
    extractor: StructuredExtractorDependency,
    producer: EventProducerDependency,
) -> JobResponse:
    """Submit a job posting URL for extraction.

    Note (flagged contract gap — see this agent's final report):
    api-contracts.md lists `user_id exists` as a validation rule producing
    404 `NOT_FOUND`, but dependency-graph.md documents zero runtime API
    calls from Job Ingestion Service to User Service, so this handler has
    no documented way to verify `user_id` exists and does not attempt to.
    `user_id` is now token-derived rather than client-supplied (see
    `infrastructure.auth`), which closes the "client can claim any
    user_id" gap but not this one — a valid token's user_id still isn't
    checked for existence in the `users` table.
    """
    logger.info(
        "Job ingestion requested | %s",
        format_context(user_id=user_id, url=request.url),
    )
    try:
        job = await ingest_job_url(
            user_id=user_id,
            url=request.url,
            repository=repository,
            page_fetcher=page_fetcher,
            extractor=extractor,
            publish=lambda normalized: publish_job_discovered(
                normalized, producer=producer
            ),
        )
    except JobIngestionError as error:
        logger.error(
            "Job ingestion failed | %s",
            format_context(user_id=user_id, error_code=error.error_code.value, error=error.message),
        )
        raise _http_error(error) from error

    logger.info(
        "Job created | %s",
        format_context(job_id=job.id, user_id=user_id, status=job.processing_status.value),
    )
    return _job_response(job)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job_route(
    job_id: UUID,
    repository: JobRepositoryDependency,
) -> JobResponse:
    """Read one job's canonical detail. See api-contracts.md#job-ingestion-service
    for why this lives here rather than on Job Matching Service (the `jobs`
    table's actual owner/writer, shared between this service and Job
    Discovery Service — see database-ownership.md#jobs)."""
    job = await repository.get(JobId(job_id))
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": ErrorCode.NOT_FOUND.value, "message": f"job {job_id} not found"},
        )
    return _job_response(job)


__all__ = ["router"]
