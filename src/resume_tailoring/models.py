"""Database record type owned by Resume Tailoring Service, plus the domain
type its repository projects onto (docs/tailored-resume/TECH.md Task 5).

One `tailored_resumes` row is written per *successful* generation — a run
that cleared `resume_tailoring.service.MINIMUM_SCORE_IMPROVEMENT`. A job
with no row has either never been tailored or produced a result that did
not clear the gate (`improved=False`, which persists nothing but is logged
by the service, per TECH.md Task 5's acceptance criteria).

`TailoredResume` (the domain type below) deliberately lives here rather
than in `shared/types/domain/`: that package holds the system's *shared*
contracts — entities another component reads or receives on an event — and
nothing outside this component consumes a tailored resume except through
this feature's own API routes (TECH.md Task 6). CLAUDE.md's "check whether
an existing shared contract already exists before introducing a shared
model" rule points the same way: no existing shared type covers this, and
this row does not need to become one to ship the feature. `id` is likewise
a plain `UUID` rather than a new `NewType` in `shared.types.ids`, which is
a locked contract listing the system's canonical entities — the same
precedent `shared.types.domain.user_llm_config.UserLlmConfig` records for a
per-feature row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import DateTime, Float, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base
from shared.types.ids import JobId, ResumeId, UserId


class TailoredResumeRecord(Base):
    """Maps to the `tailored_resumes` table. May create/update: Resume
    Tailoring Service only.

    `user_id`/`job_id`/`source_resume_id` are *logical* foreign keys only
    (no DB-enforced `ForeignKey(...)`) to other components' tables
    (`users`, `jobs`, `resumes`) — the same precedent
    `matching/models.py:JobMatchRecord` and `outreach/models.py:
    OutreachRecord` set and document: a real `ForeignKey` would force this
    component's own tests to import three other components' `models.py`
    just to stand up a database, a heavier coupling than the domain model
    requires.

    Append-only in practice. Nothing re-generates today, but the schema
    carries no uniqueness constraint beyond the primary key, so a future
    regeneration inserts a new row rather than mutating history — which is
    why `TailoredResumeRepository.get_for_job` orders by `created_at`
    descending and takes the newest.

    `storage_uri` holds whatever `LocalTailoredResumeStorage.save` returned
    (a local filesystem path today), exactly as `resumes.storage_uri` does
    for uploaded files — the column is a plain string so a future object
    -store backend needs no schema change.
    """

    __tablename__ = "tailored_resumes"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    source_resume_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    # The `JobMatch.match_score` this generation started from, and the score
    # `score_profile_with_llm` gave the tailored content. Both are stored so
    # the "+9 points" the user was shown at generation time can be shown
    # again on a later read without re-running either LLM call.
    base_score: Mapped[float] = mapped_column(Float, nullable=False)
    achieved_score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class TailoredResume(BaseModel):
    """A generated, stored tailored resume — what the repository returns and
    what the API routes (TECH.md Task 6) project into their responses.

    Carries no document bytes: `storage_uri` is the handle, and
    `LocalTailoredResumeStorage.read` is the only way to the content, so
    this type stays cheap to log, compare, and serialize.
    """

    id: UUID
    user_id: UserId
    job_id: JobId
    source_resume_id: ResumeId
    storage_uri: str
    base_score: float
    achieved_score: float
    created_at: datetime


__all__ = ["TailoredResume", "TailoredResumeRecord"]
