"""Job Ingestion Service and Job Discovery Service API request/response
contracts. See docs/architecture/api-contracts.md#job-ingestion-service and
docs/architecture/api-contracts.md#job-discovery-service.

`JobResponse`'s `location`/`description`/`extracted_skills`/
`experience_required`/`source_url` fields (Step 10.5 frontend-blocking
contract cleanup) close a gap between this type and api-contracts.md's own
`GET /jobs/{job_id}` prose, which always documented them as part of the
response but the type never carried. Additive-only: every field maps
1:1 onto an already-persisted `shared.types.domain.job.Job` field (no new
domain data, no schema change) — `description` mirrors `Job.description_raw`
under the name already used by `shared.types.dto.NormalizedJob`, so the
same concept isn't given two different field names across the two
canonical types that carry it.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from shared.types.enums import JobProcessingStatus, JobSourceType
from shared.types.ids import JobId, JobSourceId, UserId


class IngestJobUrlRequest(BaseModel):
    url: str


class JobResponse(BaseModel):
    id: JobId
    user_id: UserId
    company: str
    title: str
    location: str | None = None
    description: str
    extracted_skills: list[str] = []
    experience_required: str | None = None
    source_url: str | None = None
    processing_status: JobProcessingStatus
    discovered_at: datetime


class CreateJobSourceRequest(BaseModel):
    name: str
    type: JobSourceType
    query_config: dict[str, Any] | None = None
    enabled: bool


class JobSourceResponse(BaseModel):
    id: JobSourceId
    user_id: UserId
    name: str
    type: JobSourceType
    query_config: dict[str, Any] | None = None
    enabled: bool
    last_run_at: datetime | None = None


__all__ = [
    "CreateJobSourceRequest",
    "IngestJobUrlRequest",
    "JobResponse",
    "JobSourceResponse",
]
