"""Database record types for the shared `jobs` table plus Job Discovery
Service's `job_sources` table.
See docs/architecture/database-ownership.md#jobs and #job_sources.

`JobRecord` is the one entity two components both create into: Job
Ingestion Service (source_type=MANUAL_URL) and Job Discovery Service (all
other source_type values). Job Matching Service holds a narrow update
right on `processing_status` only — see
docs/architecture/ownership.md#shared-write-jobs-table. No other write
pattern is permitted.

Field shapes are locked in docs/architecture/domain-model.md#job and
#jobsource — do not diverge from them when implementing.

`user_id` is a logical foreign key only (no DB-enforced constraint), per
domain-model.md's note that cross-component references are not necessarily
DB-enforced. `source_id` is a real FK because `job_sources` is owned by the
same package.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base
from shared.types.enums import JobProcessingStatus, JobSourceType

# Stored as VARCHAR + CHECK rather than a native PostgreSQL ENUM type:
# enum values are additive-only (shared-types.md#versioning-rules), and a
# non-native enum makes adding a JobSourceType a code change rather than a
# DDL migration on a shared type.
_JOB_SOURCE_TYPE = SAEnum(
    JobSourceType, name="job_source_type", native_enum=False, length=32
)
_JOB_PROCESSING_STATUS = SAEnum(
    JobProcessingStatus, name="job_processing_status", native_enum=False, length=32
)


class JobRecord(Base):
    """Maps to the `jobs` table. May create: Job Ingestion Service
    (source_type=MANUAL_URL), Job Discovery Service (all other
    source_type). May update: Job Matching Service, `processing_status`
    column only.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        # Supports the manual-path duplicate lookup (same canonical URL
        # already ingested for this user) without a full scan.
        Index("ix_jobs_user_id_source_url", "user_id", "source_url"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source_type: Mapped[JobSourceType] = mapped_column(
        _JOB_SOURCE_TYPE, nullable=False
    )
    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("job_sources.id"), nullable=True
    )
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    company: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_raw: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_skills: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    experience_required: Mapped[str | None] = mapped_column(String(255), nullable=True)
    processing_status: Mapped[JobProcessingStatus] = mapped_column(
        _JOB_PROCESSING_STATUS, nullable=False
    )
    discovered_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)


class JobSourceRecord(Base):
    """Maps to the `job_sources` table. May create/update: Job Discovery
    Service only.
    """

    __tablename__ = "job_sources"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[JobSourceType] = mapped_column(_JOB_SOURCE_TYPE, nullable=False)
    query_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_run_at: Mapped[Any | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["JobRecord", "JobSourceRecord"]
