"""Database record types owned by Contact Discovery Service.
See docs/architecture/database-ownership.md#contacts and #contact_rankings.

Field shapes are locked in docs/architecture/domain-model.md#contact and
#contactscore — do not diverge from them when implementing.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Float, String, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base
from shared.types.enums import ContactStatus, ContactType

# Stored as VARCHAR + CHECK rather than a native PostgreSQL ENUM type, same
# rationale as matching/models.py's _MATCH_RECOMMENDATION: enum values are
# additive-only (shared-types.md#versioning-rules), so a non-native enum
# makes adding a member a code change rather than a DDL migration on a
# shared type.
_CONTACT_TYPE = SAEnum(ContactType, name="contact_type", native_enum=False, length=32)
_CONTACT_STATUS = SAEnum(ContactStatus, name="contact_status", native_enum=False, length=32)


class ContactRecord(Base):
    """Maps to the `contacts` table. May create/update: Contact Discovery
    Service only.

    `job_id`/`user_id` are *logical* foreign keys only (no DB-enforced
    `ForeignKey(...)`) to other components' tables (`jobs`, `users`) — same
    precedent as `matching/models.py:JobMatchRecord`'s identical
    cross-component reference shape (see that module's docstring for the
    full rationale: avoids forcing this component's tests/migrations to
    import another component's `models.py`).
    """

    __tablename__ = "contacts"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    headline: Mapped[str | None] = mapped_column(String, nullable=True)
    company: Mapped[str] = mapped_column(String, nullable=False)
    contact_type: Mapped[ContactType] = mapped_column(_CONTACT_TYPE, nullable=False)
    profile_url: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[ContactStatus] = mapped_column(_CONTACT_STATUS, nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ContactScoreRecord(Base):
    """Maps to the `contact_rankings` table. May create/update: Contact
    Discovery Service only. May read: Contact Discovery Service only —
    score breakdown is internal detail; only `relevance_score` leaves via
    `ContactsFoundEvent` (database-ownership.md#contact_rankings).
    """

    __tablename__ = "contact_rankings"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    contact_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    job_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False)
    same_company: Mapped[bool] = mapped_column(Boolean, nullable=False)
    department_relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    role_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    seniority_fit: Mapped[float | None] = mapped_column(Float, nullable=True)
    ranked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["ContactRecord", "ContactScoreRecord"]
