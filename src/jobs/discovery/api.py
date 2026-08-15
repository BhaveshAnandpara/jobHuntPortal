"""Job Discovery Service API router.

Owned endpoints (docs/architecture/api-contracts.md#job-discovery-service):
    POST /job-sources
    GET  /job-sources

Mounted into the app in api/main.py.
"""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from infrastructure.auth import CurrentUserIdDependency
from jobs.discovery.dependencies import get_job_source_repository
from jobs.discovery.errors import JobDiscoveryError
from jobs.repository import JobSourceRepository
from shared.errors.codes import ErrorCode
from shared.types.api.jobs import CreateJobSourceRequest, JobSourceResponse
from shared.types.domain.job_source import JobSource
from shared.types.ids import JobSourceId

router = APIRouter(tags=["job-discovery"])

JobSourceRepositoryDependency = Annotated[
    JobSourceRepository, Depends(get_job_source_repository)
]

_STATUS_BY_ERROR_CODE = {
    ErrorCode.VALIDATION_ERROR: status.HTTP_400_BAD_REQUEST,
}


def _http_error(error: JobDiscoveryError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_BY_ERROR_CODE.get(error.error_code, status.HTTP_400_BAD_REQUEST),
        detail={"code": error.error_code.value, "message": error.message},
    )


def _source_response(source: JobSource) -> JobSourceResponse:
    return JobSourceResponse(
        id=source.id,
        user_id=source.user_id,
        name=source.name,
        type=source.type,
        query_config=source.query_config,
        enabled=source.enabled,
        last_run_at=source.last_run_at,
    )


@router.post(
    "/job-sources",
    response_model=JobSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_job_source(
    request: CreateJobSourceRequest,
    user_id: CurrentUserIdDependency,
    repository: JobSourceRepositoryDependency,
) -> JobSourceResponse:
    name = request.name.strip()
    if not name:
        raise _http_error(
            JobDiscoveryError(ErrorCode.VALIDATION_ERROR, "name must not be empty")
        )

    source = JobSource(
        id=JobSourceId(uuid4()),
        user_id=user_id,
        name=name,
        type=request.type,
        query_config=request.query_config,
        enabled=request.enabled,
        last_run_at=None,
    )
    await repository.insert(source)
    return _source_response(source)


@router.get("/job-sources", response_model=list[JobSourceResponse])
async def list_job_sources(
    user_id: CurrentUserIdDependency,
    repository: JobSourceRepositoryDependency,
) -> list[JobSourceResponse]:
    sources = await repository.list_for_user(user_id)
    return [_source_response(source) for source in sources]


__all__ = ["router"]
