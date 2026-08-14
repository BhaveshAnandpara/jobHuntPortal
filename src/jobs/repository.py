"""Persistence access for the `jobs` and `job_sources` tables.

Ownership grants (docs/architecture/ownership.md#shared-write-jobs-table)
are enforced here in code, not merely by convention:

    insert_manual()     -> Job Ingestion Service, source_type=MANUAL_URL only
    insert_discovered() -> Job Discovery Service, any other source_type

Neither service may update a `jobs` row after creation; the only update
right on this table belongs to Job Matching Service
(`processing_status` column) and is not exposed by this module.

Runs on the shared `AsyncSession` from `infrastructure/database/` (owned by
the Database Agent) — see
docs/architecture/dependency-graph.md#4-database-dependencies. Uses
`AsyncSession` (not a sync `Session`) to match
`infrastructure.database.get_session`'s async-generator contract, the same
pattern `users/repository.py` and `profiles/repository.py` follow. Methods
`flush()` rather than `commit()`: the owning request/call-scoped session
(from `get_session()`) commits on clean exit, so repositories never
finalize a transaction a caller might still be composing further writes
into.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jobs.models import JobRecord, JobSourceRecord
from shared.types.domain.job import Job
from shared.types.domain.job_source import JobSource
from shared.types.enums import JobSourceType
from shared.types.ids import JobId, JobSourceId, UserId


def _to_job(record: JobRecord) -> Job:
    return Job.model_validate(record, from_attributes=True)


def _to_job_source(record: JobSourceRecord) -> JobSource:
    return JobSource.model_validate(record, from_attributes=True)


class JobRepository:
    """Writes to the shared `jobs` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, job_id: JobId) -> Job | None:
        """Read-only lookup by id, backing `GET /jobs/{job_id}`
        (api-contracts.md#job-ingestion-service) — a plain read, not a
        write, so it needs no `source_type`/writer-identity guard like
        `insert_manual`/`insert_discovered` above.
        """
        record = await self._session.get(JobRecord, UUID(str(job_id)))
        return _to_job(record) if record is not None else None

    async def find_by_source_url(self, user_id: UserId, source_url: str) -> Job | None:
        """Look up an existing job for this user by canonical source URL.

        Both ingestion paths use this for deduplication; `source_url` is
        expected to already be in canonical form (see
        `jobs.ingestion.service.canonicalize_url`).
        """
        result = await self._session.execute(
            select(JobRecord)
            .where(JobRecord.user_id == UUID(str(user_id)))
            .where(JobRecord.source_url == source_url)
            .limit(1)
        )
        record = result.scalars().first()
        return _to_job(record) if record is not None else None

    async def insert_manual(self, job: Job) -> None:
        """Insert a manually ingested job. Job Ingestion Service only."""
        if job.source_type is not JobSourceType.MANUAL_URL:
            raise ValueError(
                "Job Ingestion Service may only insert source_type=MANUAL_URL rows "
                f"(got {job.source_type})"
            )
        await self._insert(job)

    async def insert_discovered(self, job: Job) -> None:
        """Insert an automatically discovered job. Job Discovery Service only."""
        if job.source_type is JobSourceType.MANUAL_URL:
            raise ValueError(
                "Job Discovery Service may not insert source_type=MANUAL_URL rows; "
                "that grant belongs to Job Ingestion Service"
            )
        await self._insert(job)

    async def _insert(self, job: Job) -> None:
        self._session.add(JobRecord(**job.model_dump()))
        await self._session.flush()


class JobSourceRepository:
    """Writes to `job_sources`. Job Discovery Service only."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(self, source: JobSource) -> None:
        self._session.add(JobSourceRecord(**source.model_dump()))
        await self._session.flush()

    async def get(self, source_id: JobSourceId) -> JobSource | None:
        record = await self._session.get(JobSourceRecord, UUID(str(source_id)))
        return _to_job_source(record) if record is not None else None

    async def list_for_user(self, user_id: UserId) -> list[JobSource]:
        result = await self._session.execute(
            select(JobSourceRecord)
            .where(JobSourceRecord.user_id == UUID(str(user_id)))
            .order_by(JobSourceRecord.name)
        )
        records = result.scalars().all()
        return [_to_job_source(record) for record in records]

    async def list_enabled(self) -> list[JobSource]:
        result = await self._session.execute(
            select(JobSourceRecord).where(JobSourceRecord.enabled.is_(True))
        )
        records = result.scalars().all()
        return [_to_job_source(record) for record in records]

    async def mark_run(self, source_id: JobSourceId, run_at: datetime) -> None:
        record = await self._session.get(JobSourceRecord, UUID(str(source_id)))
        if record is None:
            raise ValueError(f"unknown job source {source_id}")
        record.last_run_at = run_at
        await self._session.flush()


__all__ = ["JobRepository", "JobSourceRepository"]
