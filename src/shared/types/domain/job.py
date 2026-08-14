"""Job — a single opportunity as ingested (manually via URL or
automatically via discovery), before any matching decision. See
docs/architecture/domain-model.md#job.

Ownership: Job Ingestion Service (manual path) / Job Discovery Service
(automatic path) — both write to the same `jobs` table using the same
schema. See docs/architecture/ownership.md#shared-write-jobs-table.
Modifiable by: Job Ingestion Service, Job Discovery Service (creation only),
Job Matching Service (processing_status column only, after matching).
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.enums import JobProcessingStatus, JobSourceType
from shared.types.ids import JobId, JobSourceId, UserId


class Job(BaseModel):
    id: JobId
    user_id: UserId  # the user this job was discovered/ingested for
    source_type: JobSourceType  # MANUAL_URL or an automatic source
    # set when source_type is an automatic source; references JobSource
    source_id: JobSourceId | None = None
    source_url: str | None = None  # original URL; required if MANUAL_URL
    company: str
    title: str
    location: str | None = None
    description_raw: str  # full extracted job text
    extracted_skills: list[str] = []
    experience_required: str | None = None  # free text, e.g. "3-5 years"
    processing_status: JobProcessingStatus  # ingestion-internal status
    discovered_at: datetime
