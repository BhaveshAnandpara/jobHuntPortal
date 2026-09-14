"""Persistence boundary for User Service's owned tables (`users`,
`user_preferences`, `user_llm_config`, `user_skill_prompts`).

No other component may import this module — see
docs/architecture/ownership.md#rule-prefer-a-contract-over-reaching-into-internal-state.
Repositories translate between the canonical domain types
(`shared.types.domain.user`, `shared.types.domain.user_preferences`,
`shared.types.domain.user_llm_config`) and this component's `*Record`
SQLAlchemy models; nothing above this layer sees a `Record`.

This module is also the *only* place that encrypts or decrypts a stored
LLM API key (`infrastructure.security.encryption`) — see
`UserLlmConfigRepository`.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.security.encryption import EncryptionError, decrypt, encrypt
from shared.types.domain.user import User
from shared.types.domain.user_llm_config import LlmProviderName, UserLlmConfig
from shared.types.domain.skill_prompt import SkillPromptCustomization
from shared.types.domain.user_preferences import UserPreferences
from shared.types.ids import UserId, UserPreferencesId
from users.models import (
    UserLlmConfigRecord,
    UserPreferencesRecord,
    UserRecord,
    UserSkillPromptsRecord,
)

KEY_HINT_VISIBLE_CHARS = 4
"""How many trailing characters of an API key `key_hint` reveals."""


def _to_user(record: UserRecord) -> User:
    return User(
        id=UserId(record.id),
        email=record.email,
        display_name=record.display_name,
        created_at=record.created_at,
        timezone=record.timezone,
    )


def _to_preferences(record: UserPreferencesRecord) -> UserPreferences:
    return UserPreferences(
        id=UserPreferencesId(record.id),
        user_id=UserId(record.user_id),
        target_roles=list(record.target_roles),
        target_locations=list(record.target_locations),
        remote_preference=record.remote_preference,
        excluded_companies=list(record.excluded_companies),
        min_salary=record.min_salary,
        salary_currency=record.salary_currency,
        updated_at=record.updated_at,
    )


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UserId) -> User | None:
        record = await self._session.get(UserRecord, user_id)
        return _to_user(record) if record is not None else None

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(UserRecord).where(UserRecord.email == email)
        )
        record = result.scalar_one_or_none()
        return _to_user(record) if record is not None else None

    async def get_by_email_with_hash(self, email: str) -> tuple[User, str] | None:
        """Login-only lookup. `password_hash` is kept off the `User` domain
        type everywhere else (defense in depth — general-purpose
        `UserService` methods that return a `User` to callers must never be
        able to leak a hash), so this is the one place it's read.
        """
        result = await self._session.execute(
            select(UserRecord).where(UserRecord.email == email)
        )
        record = result.scalar_one_or_none()
        return (_to_user(record), record.password_hash) if record is not None else None

    async def add(self, user: User, password_hash: str) -> User:
        record = UserRecord(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            password_hash=password_hash,
            created_at=user.created_at,
            timezone=user.timezone,
        )
        self._session.add(record)
        await self._session.flush()
        return _to_user(record)


class UserPreferencesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UserId) -> UserPreferences | None:
        result = await self._session.execute(
            select(UserPreferencesRecord).where(UserPreferencesRecord.user_id == user_id)
        )
        record = result.scalar_one_or_none()
        return _to_preferences(record) if record is not None else None

    async def upsert(self, preferences: UserPreferences) -> UserPreferences:
        """One active row per user — an existing row for `user_id` is
        replaced in place, keeping its `id`, rather than inserting a second.
        """
        result = await self._session.execute(
            select(UserPreferencesRecord).where(
                UserPreferencesRecord.user_id == preferences.user_id
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            record = UserPreferencesRecord(id=preferences.id, user_id=preferences.user_id)
            self._session.add(record)

        record.target_roles = list(preferences.target_roles)
        record.target_locations = list(preferences.target_locations)
        record.remote_preference = preferences.remote_preference
        record.excluded_companies = list(preferences.excluded_companies)
        record.min_salary = preferences.min_salary
        record.salary_currency = preferences.salary_currency
        record.updated_at = preferences.updated_at

        await self._session.flush()
        return _to_preferences(record)


def _mask_api_key(api_key: str) -> str | None:
    """Return the masked hint shown in place of a stored key.

    `...ab12` — an ellipsis plus the last four characters, which is enough
    for a user to recognize which of their keys is saved and useless to
    anyone else. A key short enough that four characters would be a
    meaningful fraction of it (or an empty one) reveals nothing at all.
    """
    if not api_key:
        return None
    if len(api_key) <= KEY_HINT_VISIBLE_CHARS:
        return "..."
    return f"...{api_key[-KEY_HINT_VISIBLE_CHARS:]}"


def _to_llm_config(record: UserLlmConfigRecord) -> UserLlmConfig:
    """Build the masked domain object from a stored row.

    Decrypts only to compute `key_hint`, and only the tail of the result
    ever leaves this function — the plaintext is never placed on the
    returned object. A row whose ciphertext no longer decrypts (typically
    `LLM_CONFIG_ENCRYPTION_KEY` was rotated) is still returned, with
    `key_hint=None`, so the user can see *that* a config exists and
    re-enter their key; the call path that actually needs the key
    (`get_decrypted_api_key`) fails loudly instead.
    """
    has_key = bool(record.encrypted_api_key)
    key_hint: str | None = None
    if has_key:
        try:
            key_hint = _mask_api_key(decrypt(record.encrypted_api_key))
        except EncryptionError:
            key_hint = None

    return UserLlmConfig(
        id=record.id,
        user_id=UserId(record.user_id),
        provider=record.provider,
        model=record.model,
        has_key=has_key,
        key_hint=key_hint,
        last_validated_at=record.last_validated_at,
        updated_at=record.updated_at,
    )


class UserLlmConfigRepository:
    """Stores one LLM provider configuration per user, with the API key
    encrypted at rest.

    Takes the plaintext key as a primitive argument rather than on a domain
    object, because `UserLlmConfig` deliberately cannot carry one (see that
    type's docstring). This class is the only encryption/decryption site
    for it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UserId) -> UserLlmConfig | None:
        """Return the user's config with the key masked, or None if unset."""
        record = await self._get_record(user_id)
        return _to_llm_config(record) if record is not None else None

    async def get_decrypted_api_key(self, user_id: UserId) -> str | None:
        """Return the user's real, usable API key in plaintext.

        NEVER expose this value in an API response, log line, error
        message, Kafka event, or any domain object. Its one legitimate
        caller is the function that builds a provider client for an actual
        LLM call (`get_user_llm_client`, see
        docs/llm-provider-config/TECH.md Task 5) — everything else must use
        `get`, which returns the masked `UserLlmConfig`.

        Returns None when the user has no config row or the row stores no
        key. Raises `EncryptionError` if a stored ciphertext cannot be
        decrypted — a caller that cannot get the real key must not silently
        proceed as if it had.
        """
        record = await self._get_record(user_id)
        if record is None or not record.encrypted_api_key:
            return None
        return decrypt(record.encrypted_api_key)

    async def upsert(
        self,
        user_id: UserId,
        provider: LlmProviderName,
        model: str | None,
        api_key: str,
        last_validated_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> UserLlmConfig:
        """One active row per user — an existing row for `user_id` is
        replaced in place, keeping its `id`, rather than inserting a second.

        `api_key` is the plaintext key; it is encrypted here and the
        plaintext is never written to the database.

        Full-replace semantics, matching `/users/me/preferences`: every
        field is written from the arguments, so an upsert that omits
        `last_validated_at` clears it. A newly-submitted key has not been
        validated until its caller says so.
        """
        record = await self._get_record(user_id)
        if record is None:
            record = UserLlmConfigRecord(id=uuid4(), user_id=user_id)
            self._session.add(record)

        record.provider = provider
        record.model = model
        record.encrypted_api_key = encrypt(api_key)
        record.last_validated_at = last_validated_at
        record.updated_at = updated_at if updated_at is not None else datetime.now(UTC)

        await self._session.flush()
        return _to_llm_config(record)

    async def _get_record(self, user_id: UserId) -> UserLlmConfigRecord | None:
        result = await self._session.execute(
            select(UserLlmConfigRecord).where(UserLlmConfigRecord.user_id == user_id)
        )
        return result.scalar_one_or_none()


def _to_skill_prompts(record: UserSkillPromptsRecord) -> SkillPromptCustomization:
    """Build the domain object from a stored row.

    `dict(...)` rather than the mapped attribute itself: SQLAlchemy hands
    back the mutable object it tracks for the JSON column, and letting that
    escape onto a domain object would mean a caller mutating the returned
    map could dirty the session.
    """
    return SkillPromptCustomization(
        user_id=UserId(record.user_id),
        instructions=dict(record.instructions),
        updated_at=record.updated_at,
    )


class SkillPromptRepository:
    """Stores one per-skill custom-instructions map per user.

    No encryption, no masking, no write-only field — the contrast with
    `UserLlmConfigRepository` is deliberate and is the whole reason this is
    a separate, much smaller class: an API key is a secret whose exposure
    has consequences, whereas this is the user's own stated preference
    about their own resume, which they typed and are entitled to read back
    verbatim.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UserId) -> SkillPromptCustomization | None:
        """Return the user's saved instructions, or None if they have none.

        None means "this user has never saved any customization" and is the
        expected answer for almost every user — callers treat it exactly
        like an empty map (use the default prompt), so it is never an
        error.
        """
        record = await self._get_record(user_id)
        return _to_skill_prompts(record) if record is not None else None

    async def upsert(
        self,
        user_id: UserId,
        instructions: dict[str, str],
        updated_at: datetime | None = None,
    ) -> SkillPromptCustomization:
        """One active row per user — an existing row for `user_id` is
        replaced in place, keeping its `id`, rather than inserting a second.

        Full-replace semantics, matching `UserPreferencesRepository.upsert`
        and `UserLlmConfigRepository.upsert`: the stored map becomes exactly
        `instructions`, so a skill omitted from the argument is *cleared*,
        not left at its previous value. That is what lets the frontend clear
        one skill's customization by submitting the complete current map
        with that entry empty, with no separate delete endpoint and no
        ambiguity between "field omitted" and "field cleared".

        The map is copied on the way in for the same reason `_to_skill_
        prompts` copies on the way out — the caller's dict must not become
        the session's tracked object.
        """
        record = await self._get_record(user_id)
        if record is None:
            record = UserSkillPromptsRecord(id=uuid4(), user_id=user_id)
            self._session.add(record)

        record.instructions = dict(instructions)
        record.updated_at = updated_at if updated_at is not None else datetime.now(UTC)

        await self._session.flush()
        return _to_skill_prompts(record)

    async def _get_record(self, user_id: UserId) -> UserSkillPromptsRecord | None:
        result = await self._session.execute(
            select(UserSkillPromptsRecord).where(UserSkillPromptsRecord.user_id == user_id)
        )
        return result.scalar_one_or_none()


__all__ = [
    "KEY_HINT_VISIBLE_CHARS",
    "SkillPromptRepository",
    "UserLlmConfigRepository",
    "UserPreferencesRepository",
    "UserRepository",
]
