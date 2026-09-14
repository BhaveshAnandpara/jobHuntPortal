"""Persistence boundary for Resume Tailoring Service's owned table
(`tailored_resumes`).

No other component may import this module — see
docs/architecture/ownership.md#rule-prefer-a-contract-over-reaching-into-internal-state.
The tailored resume a user sees is served by this feature's own API routes
(docs/tailored-resume/TECH.md Task 6), never by another component querying
this table.

Takes and returns the domain type (`resume_tailoring.models.TailoredResume`);
the `TailoredResumeRecord` SQLAlchemy model never leaves this module —
mirrors `profiles/repository.py` and `matching/repository.py`'s
`_to_domain`-style projection pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from resume_tailoring.models import TailoredResume, TailoredResumeRecord
from shared.types.ids import JobId, ResumeId, UserId


def _to_domain(record: TailoredResumeRecord) -> TailoredResume:
    return TailoredResume(
        id=record.id,
        user_id=UserId(record.user_id),
        job_id=JobId(record.job_id),
        source_resume_id=ResumeId(record.source_resume_id),
        storage_uri=record.storage_uri,
        base_score=record.base_score,
        achieved_score=record.achieved_score,
        created_at=record.created_at,
    )


class TailoredResumeRepository:
    """Reads/writes only `tailored_resumes`. Append-only: one row per
    successful (`improved=True`) generation, never updated in place.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        user_id: UserId,
        job_id: JobId,
        source_resume_id: ResumeId,
        storage_uri: str,
        base_score: float,
        achieved_score: float,
        created_at: datetime | None = None,
    ) -> TailoredResume:
        """Record one successfully generated document.

        Takes the fields rather than an assembled `TailoredResume` because
        `id` and `created_at` are this layer's to mint — the caller has just
        written a file and knows nothing else about the row. `created_at`
        is nonetheless accepted so a test (and, one day, a backfill) can
        control it; left `None`, it is "now", in UTC.
        """
        record = TailoredResumeRecord(
            id=uuid4(),
            user_id=UUID(str(user_id)),
            job_id=UUID(str(job_id)),
            source_resume_id=UUID(str(source_resume_id)),
            storage_uri=storage_uri,
            base_score=base_score,
            achieved_score=achieved_score,
            created_at=created_at if created_at is not None else datetime.now(UTC),
        )
        self._session.add(record)
        await self._session.flush()
        return _to_domain(record)

    async def get(self, tailored_resume_id: UUID) -> TailoredResume | None:
        record = await self._session.get(TailoredResumeRecord, tailored_resume_id)
        return _to_domain(record) if record is not None else None

    async def get_for_job(self, user_id: UserId, job_id: JobId) -> TailoredResume | None:
        """The most recent tailored resume this user has for `job_id`, or
        `None` when none has been generated.

        `None` is an ordinary answer, not an error — "this job hasn't been
        tailored for (successfully) yet" is the common case, the same
        "unset is not an error" convention the rest of this app's read paths
        use.

        Scoped by `user_id` as well as `job_id` so one user's row can never
        be served for another user's request, even though `job_id` is
        already per-user in this system. Ordered newest-first and limited to
        one because nothing forbids a second row for the same job (see
        models.py) — a regeneration must win over the row it replaced.
        """
        result = await self._session.scalars(
            select(TailoredResumeRecord)
            .where(
                TailoredResumeRecord.user_id == UUID(str(user_id)),
                TailoredResumeRecord.job_id == UUID(str(job_id)),
            )
            .order_by(TailoredResumeRecord.created_at.desc())
            .limit(1)
        )
        record = result.first()
        return _to_domain(record) if record is not None else None

    async def count_for_job(self, user_id: UserId, job_id: JobId) -> int:
        """How many tailored resumes this user has generated for `job_id`.

        The table is append-only (see models.py), so this count *is* the
        version number of the row `get_for_job` returns: one row means the
        first generation, three rows mean the newest document is v3. That is
        what lets the download route name a regenerated file `..._v3.docx`
        without a schema change — the history is already in the table, it
        was simply never counted.

        Scoped by `user_id` as well as `job_id` for the same
        non-disclosure reason `get_for_job` is: another user's history must
        not be observable, not even as a number.
        """
        total = await self._session.scalar(
            select(func.count())
            .select_from(TailoredResumeRecord)
            .where(
                TailoredResumeRecord.user_id == UUID(str(user_id)),
                TailoredResumeRecord.job_id == UUID(str(job_id)),
            )
        )
        return int(total or 0)


__all__ = ["TailoredResumeRepository"]
