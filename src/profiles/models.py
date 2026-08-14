"""Database record types owned by Resume/Profile Service.
See docs/architecture/database-ownership.md#resumes and #candidate_profiles.

Field shapes are locked in docs/architecture/domain-model.md#resume and
#candidateprofile. The declarative Base comes from infrastructure/database/,
owned by the Database Agent — this module only defines the two tables
Resume/Profile Service owns.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base
from shared.types.enums import ProfileStatus, ResumeStatus


class ResumeRecord(Base):
    """Maps to the `resumes` table. May create/update: Resume/Profile
    Service only.
    """

    __tablename__ = "resumes"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False, index=True
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ResumeStatus] = mapped_column(
        Enum(ResumeStatus, name="resume_status"), nullable=False
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CandidateProfileRecord(Base):
    """Maps to the `candidate_profiles` table. May create/update:
    Resume/Profile Service only.

    Every list column is JSON rather than a dialect-specific array type so
    the same schema runs on PostgreSQL and on SQLite in tests. `education`
    holds serialized `EducationEntry` objects.

    No column here encodes a profession — `skills`, `industries`,
    `target_roles`, and `seniority` are free text inferred per resume, which
    is what keeps the platform profession-independent (about_project.md,
    docs/architecture/domain-model.md#candidateprofile).
    """

    __tablename__ = "candidate_profiles"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False, index=True
    )
    resume_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("resumes.id"), nullable=False, unique=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    experience_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    seniority: Mapped[str | None] = mapped_column(String(64), nullable=True)
    education: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    certifications: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    projects: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    industries: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    target_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[ProfileStatus] = mapped_column(
        Enum(ProfileStatus, name="profile_status"), nullable=False
    )
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["CandidateProfileRecord", "ResumeRecord"]
