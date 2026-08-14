"""Business logic boundary for User Service. Called only by users/api/;
never imported by another component (see
docs/architecture/dependency-graph.md#1-compileimport-dependencies).

Preferences stay profession-independent: target roles and locations are
free text, and the only closed vocabulary is the shared
`RemoteWorkPreference` enum. No field here encodes an assumption about the
user's profession (docs/architecture/overview.md rule 5).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from shared.errors.codes import ErrorCode
from shared.types.api.users import CreateUserRequest, UpdateUserPreferencesRequest
from shared.types.domain.user import User
from shared.types.domain.user_preferences import UserPreferences
from shared.types.ids import UserId, UserPreferencesId

if TYPE_CHECKING:
    from users.repository import UserPreferencesRepository, UserRepository

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


class UserError(Exception):
    """Typed User Service failure carrying a shared `ErrorCode`, which the
    API layer maps to an HTTP status (see
    docs/architecture/api-contracts.md#user-service).
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class UserService:
    """Orchestrates UserRepository / UserPreferencesRepository on behalf of
    users/api/.
    """

    def __init__(
        self,
        users: UserRepository,
        preferences: UserPreferencesRepository,
    ) -> None:
        self._users = users
        self._preferences = preferences

    async def create_user(self, request: CreateUserRequest) -> User:
        email = request.email.strip().lower()
        if not _EMAIL_PATTERN.match(email):
            raise UserError(ErrorCode.VALIDATION_ERROR, "email is not a valid address")

        display_name = request.display_name.strip()
        if not display_name:
            raise UserError(ErrorCode.VALIDATION_ERROR, "display_name must not be empty")

        if await self._users.get_by_email(email) is not None:
            raise UserError(ErrorCode.VALIDATION_ERROR, "email is already registered")

        tz = request.timezone.strip() if request.timezone else None

        return await self._users.add(
            User(
                id=UserId(uuid4()),
                email=email,
                display_name=display_name,
                created_at=datetime.now(UTC),
                timezone=tz or None,
            )
        )

    async def get_preferences(self, user_id: UserId) -> UserPreferences:
        preferences = await self._preferences.get(user_id)
        if preferences is None:
            raise UserError(ErrorCode.NOT_FOUND, "no preferences exist for this user")
        return preferences

    async def replace_preferences(
        self, user_id: UserId, request: UpdateUserPreferencesRequest
    ) -> UserPreferences:
        if await self._users.get(user_id) is None:
            raise UserError(ErrorCode.NOT_FOUND, "user does not exist")

        if request.min_salary is not None and request.min_salary < 0:
            raise UserError(ErrorCode.VALIDATION_ERROR, "min_salary must not be negative")

        currency = request.salary_currency.strip().upper() if request.salary_currency else None
        if currency is not None and not _CURRENCY_PATTERN.match(currency):
            raise UserError(
                ErrorCode.VALIDATION_ERROR,
                "salary_currency must be a 3-letter ISO 4217 code",
            )

        existing = await self._preferences.get(user_id)
        return await self._preferences.upsert(
            UserPreferences(
                id=existing.id if existing else UserPreferencesId(uuid4()),
                user_id=user_id,
                target_roles=_clean(request.target_roles),
                target_locations=_clean(request.target_locations),
                remote_preference=request.remote_preference,
                excluded_companies=_clean(request.excluded_companies),
                min_salary=request.min_salary,
                salary_currency=currency,
                updated_at=datetime.now(UTC),
            )
        )


def _clean(values: list[str]) -> list[str]:
    """Free-text list fields — trim and drop blanks, never interpret. Role
    and location vocabularies vary by profession and are deliberately not
    validated against any fixed set.
    """
    return [stripped for value in values if (stripped := value.strip())]


__all__ = ["UserError", "UserService"]
