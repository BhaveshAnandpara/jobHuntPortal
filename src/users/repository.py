"""Persistence boundary for User Service's owned tables (`users`,
`user_preferences`).

No other component may import this module — see
docs/architecture/ownership.md#rule-prefer-a-contract-over-reaching-into-internal-state.
Repositories translate between the canonical domain types
(`shared.types.domain.user`, `shared.types.domain.user_preferences`) and this
component's `*Record` SQLAlchemy models; nothing above this layer sees a
`Record`.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.types.domain.user import User
from shared.types.domain.user_preferences import UserPreferences
from shared.types.ids import UserId, UserPreferencesId
from users.models import UserPreferencesRecord, UserRecord


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


__all__ = ["UserPreferencesRepository", "UserRepository"]
