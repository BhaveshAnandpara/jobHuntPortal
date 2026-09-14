"""Database record types owned by User Service.
See docs/architecture/database-ownership.md#users and #user_preferences.

Field shapes are locked in docs/architecture/domain-model.md#user and
#userpreferences. The declarative Base comes from infrastructure/database/,
owned by the Database Agent — this module only defines the tables User
Service owns.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base
from shared.types.domain.user_llm_config import LlmProviderName
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


class UserLlmConfigRecord(Base):
    """Maps to the `user_llm_config` table. May create/update: User Service
    only.

    Same one-row-per-user shape as `UserPreferencesRecord` (unique, indexed
    `user_id` FK). A separate table rather than extra columns on
    `user_preferences` because `scripts/bootstrap_db.py` runs
    `create_all`, which creates missing *tables* but never emits
    `ALTER TABLE` for an existing one — see
    docs/llm-provider-config/TECH.md.

    `encrypted_api_key` holds the output of
    `infrastructure.security.encryption.encrypt` — never a plaintext key.
    Fernet tokens are base64 and grow with the plaintext, so the column is
    sized well above any realistic vendor key rather than matching one.
    """

    __tablename__ = "user_llm_config"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False, unique=True, index=True
    )
    provider: Mapped[LlmProviderName] = mapped_column(
        Enum(
            LlmProviderName,
            name="llm_provider_name",
            # Store the lowercase member *values* (`claude`, `openai`, …)
            # rather than SQLAlchemy's default of member NAMES, so the
            # persisted string is the same token `LLMConfig.provider` uses.
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted_api_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserSkillPromptsRecord(Base):
    """Maps to the `user_skill_prompts` table. May create/update: User
    Service only.

    Same one-row-per-user shape as `UserLlmConfigRecord` (unique, indexed
    `user_id` FK), and a separate table for the same reason:
    `scripts/bootstrap_db.py` runs `create_all`, which creates missing
    *tables* but never emits `ALTER TABLE` for an existing one — so adding
    columns to `users` or `user_preferences` would need a manual migration
    this project has no Alembic wiring for, while a new table appears on
    the next bootstrap run with no manual step (see
    docs/prompt-customization/TECH.md).

    `instructions` is a JSON map of `SkillKey` value -> the user's own
    instruction text. One JSON column rather than one column per skill so
    a future skill is a new key, not a schema change — the same reasoning
    `CandidateProfileRecord.contact`/`.work_history` and
    `JobMatchRecord.profile_scores` already use, and JSON rather than a
    dialect-specific type so the same schema runs on PostgreSQL and on
    SQLite in tests.

    Plain text, deliberately: unlike `UserLlmConfigRecord
    .encrypted_api_key`, this is the user's own stated preference about
    their own resume, not a credential — there is nothing here to protect
    from the user it belongs to, and encrypting it would buy nothing while
    costing a decrypt on every prompt build.
    """

    __tablename__ = "user_skill_prompts"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False, unique=True, index=True
    )
    instructions: Mapped[dict[str, str]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "UserLlmConfigRecord",
    "UserPreferencesRecord",
    "UserRecord",
    "UserSkillPromptsRecord",
]
