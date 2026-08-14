"""Database record types owned by Tracking Service.
See docs/architecture/database-ownership.md#applications and
#application_history.

Field shapes are locked in docs/architecture/domain-model.md#application
and #applicationhistory — do not diverge from them when implementing.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import JSON, Date, DateTime, Float, String, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base
from shared.types.enums import ApplicationStatus, EventType

# Stored as VARCHAR + CHECK rather than a native PostgreSQL ENUM type, same
# rationale as matching/models.py's _MATCH_RECOMMENDATION: enum values are
# additive-only (shared-types.md#versioning-rules), so a non-native enum
# makes adding an ApplicationStatus/EventType member a code change rather
# than a DDL migration on a shared type.
_APPLICATION_STATUS = SAEnum(
    ApplicationStatus, name="application_status", native_enum=False, length=32
)
_EVENT_TYPE = SAEnum(EventType, name="event_type", native_enum=False, length=32)


class ApplicationRecord(Base):
    """Maps to the `applications` table. May create/update: Tracking
    Service only — including updates that originate from a user's manual
    status change, which go through Tracking's own API.

    `job_id`/`user_id`/`selected_resume_id`/`referral_contact_id` are
    *logical* foreign keys only (no DB-enforced `ForeignKey(...)`) to other
    components' tables (`jobs`, `users`, `resumes`, `contacts`), matching
    `matching/models.py`'s explicit precedent for the same shape of
    cross-component reference — a real `ForeignKey` here would force this
    component's own tests to import every referenced component's
    `models.py` just to stand up a database, a heavier cross-component
    coupling than domain-model.md's relationships table requires.
    """

    __tablename__ = "applications"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    # 1:1 with Job — one Application per job_id.
    job_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, unique=True, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    # Denormalized from Job. May be "" temporarily if this row was created
    # from an out-of-order event (jobs.matched/shortlisted/etc. arriving
    # before jobs.discovered) — backfilled once jobs.discovered arrives.
    # See tracking/consumers.py's _advance docstring.
    company: Mapped[str] = mapped_column(String, nullable=False, default="")
    title: Mapped[str] = mapped_column(String, nullable=False, default="")
    status: Mapped[ApplicationStatus] = mapped_column(_APPLICATION_STATUS, nullable=False)
    selected_resume_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, default=None)
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    matched_skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    missing_skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    referral_contact_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, default=None)
    applied_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    follow_up_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    notes: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApplicationHistoryRecord(Base):
    """Maps to the `application_history` table. May create: Tracking
    Service only (insert-only, one row per transition). May update:
    nobody — append-only.
    """

    __tablename__ = "application_history"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    application_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    from_status: Mapped[ApplicationStatus | None] = mapped_column(
        _APPLICATION_STATUS, nullable=True, default=None
    )
    to_status: Mapped[ApplicationStatus] = mapped_column(_APPLICATION_STATUS, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String, nullable=False)
    source_event_type: Mapped[EventType | None] = mapped_column(
        _EVENT_TYPE, nullable=True, default=None
    )
    correlation_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, default=None)


__all__ = ["ApplicationHistoryRecord", "ApplicationRecord"]
