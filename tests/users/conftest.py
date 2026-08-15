"""Fixtures for User Service tests.

Everything here runs against SQLite in-memory or in-process fakes — no
PostgreSQL, Kafka, or LLM is required to run this package.

The `session` fixture depends on `infrastructure.database.Base` (owned by
the Database Agent) and the `aiosqlite` driver. Both are imported lazily
inside the fixture body, guarded with skips, rather than at module level:
until the Database Agent's package re-exports `Base` and/or an async
SQLite driver is installed, only the repository tests that need a real
session should skip — unit tests (fake repositories) and API tests
(dependency-overridden service) must still collect and run.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from shared.types.domain.user import User
from shared.types.domain.user_preferences import UserPreferences
from shared.types.ids import UserId
from users.service import UserService


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    pytest.importorskip(
        "aiosqlite", reason="async sqlite driver not installed; repository tests need it"
    )
    try:
        from infrastructure.database import Base
    except ImportError as exc:  # explicit ImportError catch — infrastructure.database's
        # internals raise ImportError (name-not-found), not ModuleNotFoundError
        pytest.skip(f"infrastructure.database.Base not available yet: {exc}")

    from users.models import (  # noqa: F401 — registers tables
        UserPreferencesRecord,
        UserRecord,
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as open_session:
        yield open_session

    await engine.dispose()


class FakeUserRepository:
    def __init__(self) -> None:
        self.users: dict[UserId, User] = {}
        self.password_hashes: dict[UserId, str] = {}

    async def get(self, user_id: UserId) -> User | None:
        return self.users.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        return next((u for u in self.users.values() if u.email == email), None)

    async def get_by_email_with_hash(self, email: str) -> tuple[User, str] | None:
        user = await self.get_by_email(email)
        if user is None:
            return None
        return user, self.password_hashes[user.id]

    async def add(self, user: User, password_hash: str) -> User:
        self.users[user.id] = user
        self.password_hashes[user.id] = password_hash
        return user


class FakeUserPreferencesRepository:
    def __init__(self) -> None:
        self.preferences: dict[UserId, UserPreferences] = {}

    async def get(self, user_id: UserId) -> UserPreferences | None:
        return self.preferences.get(user_id)

    async def upsert(self, preferences: UserPreferences) -> UserPreferences:
        self.preferences[preferences.user_id] = preferences
        return preferences


@pytest.fixture
def users() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def preferences() -> FakeUserPreferencesRepository:
    return FakeUserPreferencesRepository()


@pytest.fixture
def service(
    users: FakeUserRepository, preferences: FakeUserPreferencesRepository
) -> UserService:
    return UserService(users=users, preferences=preferences)
