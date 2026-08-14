"""Persistence boundary for Job Matching Service's owned table
(`job_matches`), plus its narrow, documented `jobs.processing_status`
update grant (docs/architecture/database-ownership.md#jobs'
"processing_status transition ownership" note and
docs/architecture/ownership.md#shared-write-jobs-table).

No other component may import this module. Repositories take and return
the canonical domain type (`shared.types.domain.job_match.JobMatch`); the
`JobMatchRecord` SQLAlchemy model in models.py never leaves this module —
mirrors `profiles/repository.py`'s `_to_domain`-style projection pattern.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Column, MetaData, String, Table, Uuid, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from matching.models import JobMatchRecord
from shared.types.domain.job_match import JobMatch
from shared.types.dto import ProfileMatchScore
from shared.types.enums import JobProcessingStatus
from shared.types.ids import JobId, JobMatchId, ProfileId, ResumeId, UserId


def _to_domain(record: JobMatchRecord) -> JobMatch:
    return JobMatch(
        id=JobMatchId(record.id),
        job_id=JobId(record.job_id),
        user_id=UserId(record.user_id),
        selected_profile_id=ProfileId(record.selected_profile_id),
        selected_resume_id=ResumeId(record.selected_resume_id),
        match_score=record.match_score,
        matched_skills=list(record.matched_skills),
        missing_skills=list(record.missing_skills),
        recommendation=record.recommendation,
        profile_scores=[ProfileMatchScore(**item) for item in record.profile_scores],
        matched_at=record.matched_at,
    )


class JobMatchRepository:
    """Reads/writes only `job_matches`. In practice append-only —
    re-matching (a future `profiles.updated` re-match run) inserts a new
    row rather than mutating an existing one, per
    domain-model.md#jobmatch's immutability note.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, job_match_id: JobMatchId) -> JobMatch | None:
        record = await self._session.get(JobMatchRecord, UUID(str(job_match_id)))
        return _to_domain(record) if record is not None else None

    async def get_latest_for_job(self, job_id: JobId) -> JobMatch | None:
        """Most recent `JobMatch` for `job_id`, or `None` if the job has
        never been matched. Used both by the read API
        (`GET /jobs/{job_id}/matches`) and by `matching.consumers`'
        idempotency check on replayed `jobs.discovered` messages.
        """
        result = await self._session.scalars(
            select(JobMatchRecord)
            .where(JobMatchRecord.job_id == UUID(str(job_id)))
            .order_by(JobMatchRecord.matched_at.desc())
            .limit(1)
        )
        record = result.first()
        return _to_domain(record) if record is not None else None

    async def get_latest_for_job_with_published_at(
        self, job_id: JobId
    ) -> tuple[JobMatch, datetime | None] | None:
        """Same lookup as `get_latest_for_job`, but also returns
        `JobMatchRecord.published_at` — the publish-reliability marker
        (database-ownership.md#job_matches's "Publish-reliability column"
        section). Returned alongside the domain `JobMatch` rather than
        projected onto it, since `published_at` is deliberately not part of
        the canonical `JobMatch` domain type (see models.py). Used by
        `matching.consumers`' three-way idempotency check:

            no row                          -> run the full workflow
            row exists, published_at is None -> re-publish only, then mark
            row exists, published_at is set   -> true duplicate, skip
        """
        result = await self._session.scalars(
            select(JobMatchRecord)
            .where(JobMatchRecord.job_id == UUID(str(job_id)))
            .order_by(JobMatchRecord.matched_at.desc())
            .limit(1)
        )
        record = result.first()
        if record is None:
            return None
        return _to_domain(record), record.published_at

    async def add(self, job_match: JobMatch) -> JobMatch:
        self._session.add(
            JobMatchRecord(
                id=job_match.id,
                job_id=job_match.job_id,
                user_id=job_match.user_id,
                selected_profile_id=job_match.selected_profile_id,
                selected_resume_id=job_match.selected_resume_id,
                match_score=job_match.match_score,
                matched_skills=job_match.matched_skills,
                missing_skills=job_match.missing_skills,
                recommendation=job_match.recommendation,
                profile_scores=[
                    score.model_dump(mode="json") for score in job_match.profile_scores
                ],
                matched_at=job_match.matched_at,
            )
        )
        await self._session.flush()
        return job_match

    async def mark_published(self, job_match_id: JobMatchId, published_at: datetime) -> None:
        """The one narrow, real `UPDATE` this table's otherwise-append-only
        grant permits (database-ownership.md#job_matches's "May update"
        row: "append-only for the analytical fields ... plus one narrow
        permitted update per row: `published_at`"). Called once
        `jobs.matched` (and `jobs.shortlisted`, when applicable) have both
        published successfully — never to clear/reset the column.
        """
        record = await self._session.get(JobMatchRecord, UUID(str(job_match_id)))
        if record is None:
            raise ValueError(f"no JobMatch record with id {job_match_id}")
        record.published_at = published_at
        await self._session.flush()


# ---------------------------------------------------------------------------
# Narrow, column-level write access to the shared `jobs` table
# ---------------------------------------------------------------------------

_jobs_processing_status_table = Table(
    "jobs",
    MetaData(),
    Column("id", Uuid, primary_key=True),
    Column("processing_status", String, nullable=False),
)
"""A minimal, un-mapped SQLAlchemy Core `Table` naming only the two columns
this component's grant covers (`id` for the `WHERE`, `processing_status`
for the single-column `UPDATE`). Deliberately *not* `jobs.models.JobRecord`
(owned by Job Ingestion/Job Discovery Service) — importing another
component's `models.py` is forbidden regardless of purpose
(docs/architecture/dependency-graph.md relation 1: "No component imports
another component's module. This is absolute.").

Bound to its own private `MetaData()` rather than
`infrastructure.database.Base.metadata` specifically so it is never picked
up by a `Base.metadata.create_all()` call or an Alembic autogenerate run —
this component does not own the `jobs` table's schema and must never
create or migrate it, only issue the one narrowly-scoped `UPDATE` its grant
allows (database-ownership.md#jobs' "processing_status transition
ownership" table: "NORMALIZED -> MATCHED | FAILED, Job Matching Service,
the one and only UPDATE of this column performed by any component"). See
the Job Matching Service implementation report's Issues section for why
this SQL-Core-level approach was chosen over any alternative (the task's
documented middle-ground option between "import JobRecord" and "treat as an
unimplementable gap").
"""


class JobProcessingStatusRepository:
    """The sole `UPDATE processing_status` grant on the shared `jobs`
    table (database-ownership.md#jobs). No other column, no other
    component's write path.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def mark_status(self, job_id: JobId, status: JobProcessingStatus) -> None:
        await self._session.execute(
            update(_jobs_processing_status_table)
            .where(_jobs_processing_status_table.c.id == UUID(str(job_id)))
            .values(processing_status=status.value)
        )


__all__ = ["JobMatchRepository", "JobProcessingStatusRepository"]
