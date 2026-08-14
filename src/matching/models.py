"""Database record types owned by Job Matching Service.
See docs/architecture/database-ownership.md#job_matches.

Field shapes are locked in docs/architecture/domain-model.md#jobmatch — do
not diverge from them when implementing.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Float, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base
from shared.types.enums import MatchRecommendation

# Stored as VARCHAR + CHECK rather than a native PostgreSQL ENUM type, same
# rationale as jobs/models.py's _JOB_PROCESSING_STATUS: enum values are
# additive-only (shared-types.md#versioning-rules), so a non-native enum
# makes adding a MatchRecommendation member a code change rather than a DDL
# migration on a shared type.
_MATCH_RECOMMENDATION = SAEnum(
    MatchRecommendation, name="match_recommendation", native_enum=False, length=32
)


class JobMatchRecord(Base):
    """Maps to the `job_matches` table. May create/update: Job Matching
    Service only (in practice append-only — re-matching inserts a new
    row).

    `job_id`/`user_id`/`selected_profile_id`/`selected_resume_id` are
    *logical* foreign keys only (no DB-enforced `ForeignKey(...)`) to other
    components' tables (`jobs`, `users`, `candidate_profiles`, `resumes`),
    matching `jobs/models.py`'s explicit precedent for the same shape of
    cross-component reference ("user_id is a logical foreign key only (no
    DB-enforced constraint) ... not necessarily a DB-enforced cross-schema
    FK if tables are later split"). A real `ForeignKey` here would also
    require every referenced component's `models.py` to be imported before
    `Base.metadata.create_all()` can resolve it (verified: SQLAlchemy's DDL
    sorter raises `NoReferencedTableError` otherwise) — i.e. it would force
    this component's own tests to import `jobs.models`/`users.models`/
    `profiles.models` just to stand up a database, which is a heavier
    cross-component coupling than domain-model.md's relationships table
    requires. This grants no write access to those tables regardless —
    only Job Matching Service's own `repository.py` writes to
    `job_matches`, and this component's separate, narrowly-scoped
    `processing_status` UPDATE on `jobs` is implemented at the SQL-Core
    level in `repository.py`, deliberately not through this ORM model (see
    that module for why).
    """

    __tablename__ = "job_matches"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    selected_profile_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    selected_resume_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    match_score: Mapped[float] = mapped_column(Float, nullable=False)
    matched_skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    missing_skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    recommendation: Mapped[MatchRecommendation] = mapped_column(
        _MATCH_RECOMMENDATION, nullable=False
    )
    # Score against every evaluated profile, not just the winner — a list of
    # serialized ProfileMatchScore dicts (shared-types.md's cross-cutting
    # DTO). JSON rather than a dialect-specific array type so the same
    # schema runs on PostgreSQL and SQLite in tests, matching every other
    # component's list-column convention (see profiles/models.py).
    profile_scores: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Publish-reliability marker (database-ownership.md#job_matches's
    # "Publish-reliability column" section) — NULL until jobs.matched (and
    # jobs.shortlisted, when applicable) have both been published
    # successfully; set once, never cleared. This is purely
    # JobMatchRecord/persistence-layer bookkeeping: it is deliberately NOT
    # on the canonical `JobMatch` domain type (shared.types.domain.job_match)
    # or on `JobMatchResult` (the jobs.matched/jobs.shortlisted event
    # payload) — no other component needs "has this been published yet"
    # from the event itself, and domain-model.md's JobMatch field list was
    # not changed for this fix. `JobMatchRepository._to_domain` therefore
    # does not project this column; `get_latest_for_job_with_published_at`
    # returns it alongside the domain `JobMatch` for callers that need it
    # (matching.consumers' three-way idempotency check).
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


__all__ = ["JobMatchRecord"]
