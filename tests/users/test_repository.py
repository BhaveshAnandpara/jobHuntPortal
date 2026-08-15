"""Tests for UserRepository / UserPreferencesRepository against a real
(SQLite in-memory) async SQLAlchemy session — docs/architecture/database-
ownership.md#users and #user_preferences.

Uses the `session` fixture from conftest.py, which itself skips (rather
than erroring) when `infrastructure.database.Base` isn't importable yet or
the `aiosqlite` driver isn't installed — see that fixture's docstring.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shared.types.domain.user import User
from shared.types.domain.user_preferences import UserPreferences
from shared.types.enums import RemoteWorkPreference
from shared.types.ids import UserId, UserPreferencesId

# users.repository -> users.models -> infrastructure.database.Base. Until the
# Database Agent's infrastructure/database package re-exports `Base` (see
# module docstring above and this package's final report), that chain
# raises ImportError. Skip this whole module gracefully instead of failing
# collection for the entire tests/users package.
_repository_module = pytest.importorskip(
    "users.repository",
    reason="users.models requires infrastructure.database.Base, not yet exported",
    exc_type=ImportError,  # pytest 9.1 defaults to ModuleNotFoundError only; this is a
    # name-not-found ImportError deep in infrastructure.database, still WIP
)
UserPreferencesRepository = _repository_module.UserPreferencesRepository
UserRepository = _repository_module.UserRepository


def _make_user(email: str = "person@example.com") -> User:
    return User(
        id=UserId(uuid4()),
        email=email,
        display_name="Person",
        created_at=datetime.now(UTC),
        timezone="UTC",
    )


@pytest.mark.asyncio
async def test_user_repository_add_and_get_roundtrip(session: AsyncSession) -> None:
    repo = UserRepository(session)
    user = _make_user()

    added = await repo.add(user, "hashed-password")
    fetched = await repo.get(user.id)

    assert added.id == user.id
    assert fetched is not None
    assert fetched.email == user.email
    assert fetched.display_name == user.display_name
    assert fetched.timezone == "UTC"


@pytest.mark.asyncio
async def test_user_repository_get_unknown_id_returns_none(session: AsyncSession) -> None:
    repo = UserRepository(session)

    result = await repo.get(UserId(uuid4()))

    assert result is None


@pytest.mark.asyncio
async def test_user_repository_get_by_email(session: AsyncSession) -> None:
    repo = UserRepository(session)
    user = _make_user(email="lookup@example.com")
    await repo.add(user, "hashed-password")

    found = await repo.get_by_email("lookup@example.com")
    missing = await repo.get_by_email("nobody@example.com")

    assert found is not None
    assert found.id == user.id
    assert missing is None


@pytest.mark.asyncio
async def test_user_repository_get_by_email_with_hash(session: AsyncSession) -> None:
    repo = UserRepository(session)
    user = _make_user(email="withhash@example.com")
    await repo.add(user, "the-stored-hash")

    found = await repo.get_by_email_with_hash("withhash@example.com")
    missing = await repo.get_by_email_with_hash("nobody@example.com")

    assert found is not None
    found_user, found_hash = found
    assert found_user.id == user.id
    assert found_hash == "the-stored-hash"
    assert missing is None


@pytest.mark.asyncio
async def test_preferences_repository_get_returns_none_when_absent(
    session: AsyncSession,
) -> None:
    repo = UserPreferencesRepository(session)

    result = await repo.get(UserId(uuid4()))

    assert result is None


@pytest.mark.asyncio
async def test_preferences_repository_upsert_creates_row(session: AsyncSession) -> None:
    users_repo = UserRepository(session)
    prefs_repo = UserPreferencesRepository(session)
    user = await users_repo.add(_make_user(), "hashed-password")

    prefs = UserPreferences(
        id=UserPreferencesId(uuid4()),
        user_id=user.id,
        target_roles=["AI Engineer"],
        remote_preference=RemoteWorkPreference.REMOTE,
        updated_at=datetime.now(UTC),
    )

    created = await prefs_repo.upsert(prefs)
    fetched = await prefs_repo.get(user.id)

    assert created.id == prefs.id
    assert fetched is not None
    assert fetched.target_roles == ["AI Engineer"]
    assert fetched.remote_preference == RemoteWorkPreference.REMOTE


@pytest.mark.asyncio
async def test_preferences_repository_upsert_replaces_existing_row(
    session: AsyncSession,
) -> None:
    """One active row per user_id — a second upsert must update the
    existing row in place (same id), not insert a second row.
    """
    users_repo = UserRepository(session)
    prefs_repo = UserPreferencesRepository(session)
    user = await users_repo.add(_make_user(), "hashed-password")

    first = await prefs_repo.upsert(
        UserPreferences(
            id=UserPreferencesId(uuid4()),
            user_id=user.id,
            target_roles=["AI Engineer"],
            updated_at=datetime.now(UTC),
        )
    )
    second = await prefs_repo.upsert(
        UserPreferences(
            id=UserPreferencesId(uuid4()),  # different id passed in — should be ignored
            user_id=user.id,
            target_roles=["Java Backend Engineer"],
            updated_at=datetime.now(UTC),
        )
    )

    fetched = await prefs_repo.get(user.id)

    assert second.id == first.id  # existing row's id preserved
    assert fetched is not None
    assert fetched.target_roles == ["Java Backend Engineer"]
