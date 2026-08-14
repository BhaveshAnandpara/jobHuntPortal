"""Unit tests for UserService — docs/architecture/api-contracts.md#user-service.

Runs entirely against in-process fake repositories (see conftest.py); no
database is required. Covers validation, the create_user/get_preferences/
replace_preferences behaviors, and every ErrorCode the service can raise
(VALIDATION_ERROR, NOT_FOUND).
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from shared.errors.codes import ErrorCode
from shared.types.api.users import CreateUserRequest, UpdateUserPreferencesRequest
from shared.types.domain.user import User
from shared.types.domain.user_preferences import UserPreferences
from shared.types.enums import RemoteWorkPreference
from shared.types.ids import UserId, UserPreferencesId
from users.service import UserError, UserService


def _seed_user(users, email: str = "existing@example.com") -> User:
    user = User(
        id=UserId(uuid4()),
        email=email,
        display_name="Existing User",
        created_at=datetime.now(UTC),
        timezone=None,
    )
    users.users[user.id] = user
    return user


# ---------------------------------------------------------------------------
# create_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_user_success(service: UserService) -> None:
    request = CreateUserRequest(
        email="New.User@Example.com", display_name="  New User  ", timezone="  America/New_York  "
    )

    created = await service.create_user(request)

    assert created.email == "new.user@example.com"  # normalized: stripped + lowercased
    assert created.display_name == "New User"  # stripped
    assert created.timezone == "America/New_York"  # stripped
    assert created.id is not None
    assert created.created_at is not None


@pytest.mark.asyncio
async def test_create_user_blank_timezone_becomes_none(service: UserService) -> None:
    request = CreateUserRequest(email="user@example.com", display_name="User", timezone="   ")

    created = await service.create_user(request)

    assert created.timezone is None


@pytest.mark.asyncio
async def test_create_user_no_timezone_is_none(service: UserService) -> None:
    request = CreateUserRequest(email="user2@example.com", display_name="User")

    created = await service.create_user(request)

    assert created.timezone is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "email",
    ["not-an-email", "missing-domain@", "@no-local-part.com", "no-at-sign.com", "user@nodot"],
)
async def test_create_user_rejects_invalid_email(service: UserService, email: str) -> None:
    request = CreateUserRequest(email=email, display_name="Someone")

    with pytest.raises(UserError) as exc_info:
        await service.create_user(request)

    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize("display_name", ["", "   "])
async def test_create_user_rejects_blank_display_name(
    service: UserService, display_name: str
) -> None:
    request = CreateUserRequest(email="user@example.com", display_name=display_name)

    with pytest.raises(UserError) as exc_info:
        await service.create_user(request)

    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


@pytest.mark.asyncio
async def test_create_user_rejects_duplicate_email(service: UserService, users) -> None:
    _seed_user(users, email="taken@example.com")
    request = CreateUserRequest(email="taken@example.com", display_name="Someone Else")

    with pytest.raises(UserError) as exc_info:
        await service.create_user(request)

    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


@pytest.mark.asyncio
async def test_create_user_rejects_duplicate_email_case_insensitive(
    service: UserService, users
) -> None:
    _seed_user(users, email="taken@example.com")
    request = CreateUserRequest(email="Taken@Example.com", display_name="Someone Else")

    with pytest.raises(UserError) as exc_info:
        await service.create_user(request)

    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


# ---------------------------------------------------------------------------
# get_preferences
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_preferences_not_found(service: UserService) -> None:
    with pytest.raises(UserError) as exc_info:
        await service.get_preferences(UserId(uuid4()))

    assert exc_info.value.code == ErrorCode.NOT_FOUND


@pytest.mark.asyncio
async def test_get_preferences_found(service: UserService, users, preferences) -> None:
    user = _seed_user(users)
    prefs = UserPreferences(
        id=UserPreferencesId(uuid4()),
        user_id=user.id,
        target_roles=["Backend Engineer"],
        updated_at=datetime.now(UTC),
    )
    preferences.preferences[user.id] = prefs

    result = await service.get_preferences(user.id)

    assert result.id == prefs.id
    assert result.target_roles == ["Backend Engineer"]


# ---------------------------------------------------------------------------
# replace_preferences
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_preferences_user_not_found(service: UserService) -> None:
    request = UpdateUserPreferencesRequest(target_roles=["AI Engineer"])

    with pytest.raises(UserError) as exc_info:
        await service.replace_preferences(UserId(uuid4()), request)

    assert exc_info.value.code == ErrorCode.NOT_FOUND


@pytest.mark.asyncio
async def test_replace_preferences_rejects_negative_salary(
    service: UserService, users
) -> None:
    user = _seed_user(users)
    request = UpdateUserPreferencesRequest(min_salary=-1)

    with pytest.raises(UserError) as exc_info:
        await service.replace_preferences(user.id, request)

    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize("currency", ["US", "USDD", "12D", "usd$"])
async def test_replace_preferences_rejects_invalid_currency(
    service: UserService, users, currency: str
) -> None:
    user = _seed_user(users)
    request = UpdateUserPreferencesRequest(salary_currency=currency)

    with pytest.raises(UserError) as exc_info:
        await service.replace_preferences(user.id, request)

    assert exc_info.value.code == ErrorCode.VALIDATION_ERROR


@pytest.mark.asyncio
async def test_replace_preferences_lowercase_currency_is_normalized(
    service: UserService, users
) -> None:
    user = _seed_user(users)
    request = UpdateUserPreferencesRequest(salary_currency="usd")

    result = await service.replace_preferences(user.id, request)

    assert result.salary_currency == "USD"


@pytest.mark.asyncio
async def test_replace_preferences_creates_when_none_exists(
    service: UserService, users
) -> None:
    user = _seed_user(users)
    request = UpdateUserPreferencesRequest(
        target_roles=["  AI Engineer ", "", "  Java Backend Engineer  "],
        target_locations=["  Remote  ", "   "],
        remote_preference=RemoteWorkPreference.REMOTE,
        excluded_companies=[" Acme Corp ", ""],
        min_salary=120000,
        salary_currency="usd",
    )

    result = await service.replace_preferences(user.id, request)

    assert result.user_id == user.id
    assert result.target_roles == ["AI Engineer", "Java Backend Engineer"]  # trimmed, blanks dropped
    assert result.target_locations == ["Remote"]
    assert result.remote_preference == RemoteWorkPreference.REMOTE
    assert result.excluded_companies == ["Acme Corp"]
    assert result.min_salary == 120000
    assert result.salary_currency == "USD"
    assert result.id is not None


@pytest.mark.asyncio
async def test_replace_preferences_upserts_keeping_same_id(
    service: UserService, users
) -> None:
    user = _seed_user(users)
    first = await service.replace_preferences(
        user.id, UpdateUserPreferencesRequest(target_roles=["AI Engineer"])
    )

    second = await service.replace_preferences(
        user.id, UpdateUserPreferencesRequest(target_roles=["Java Backend Engineer"])
    )

    assert second.id == first.id  # one active row per user — same id, replaced fields
    assert second.target_roles == ["Java Backend Engineer"]


@pytest.mark.asyncio
async def test_replace_preferences_allows_all_optional_fields_absent(
    service: UserService, users
) -> None:
    user = _seed_user(users)
    request = UpdateUserPreferencesRequest()

    result = await service.replace_preferences(user.id, request)

    assert result.target_roles == []
    assert result.target_locations == []
    assert result.remote_preference is None
    assert result.excluded_companies == []
    assert result.min_salary is None
    assert result.salary_currency is None


@pytest.mark.asyncio
async def test_replace_preferences_stays_profession_independent(
    service: UserService, users
) -> None:
    """target_roles/target_locations are free text — the service must not
    reject or reshape values into any fixed vocabulary. Only
    remote_preference is a closed enum (docs/architecture/domain-model.md#userpreferences).
    """
    user = _seed_user(users)
    request = UpdateUserPreferencesRequest(
        target_roles=["Mechanical Design Engineer", "CAD Engineer", "HR Business Partner"],
    )

    result = await service.replace_preferences(user.id, request)

    assert result.target_roles == [
        "Mechanical Design Engineer",
        "CAD Engineer",
        "HR Business Partner",
    ]
