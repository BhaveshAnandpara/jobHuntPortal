"""Database record types owned by User Service.
See docs/architecture/database-ownership.md#users and #user_preferences.

Field shapes are locked in docs/architecture/domain-model.md#user and
#userpreferences. The declarative Base comes from infrastructure/database/,
owned by the Database Agent — this module only defines the two tables User
Service owns.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base
from shared.types.enums import RemoteWorkPreference


class UserRecord(Base):
    """Maps to the `users` table. May create/update: User Service only."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)


class UserPreferencesRecord(Base):
    """Maps to the `user_preferences` table. May create/update: User
    Service only.

    List columns are JSON rather than a dialect-specific array type so the
    same schema runs on PostgreSQL and on SQLite in tests.
    """

    __tablename__ = "user_preferences"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False, unique=True, index=True
    )
    target_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    target_locations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    remote_preference: Mapped[RemoteWorkPreference | None] = mapped_column(
        Enum(RemoteWorkPreference, name="remote_work_preference"), nullable=True
    )
    excluded_companies: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    min_salary: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["UserPreferencesRecord", "UserRecord"]
