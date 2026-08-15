"""User Service API request/response contracts.
See docs/architecture/api-contracts.md#user-service.
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.enums import RemoteWorkPreference
from shared.types.ids import UserId, UserPreferencesId


class CreateUserRequest(BaseModel):
    email: str
    display_name: str
    password: str
    timezone: str | None = None


class UserResponse(BaseModel):
    id: UserId
    email: str
    display_name: str
    created_at: datetime


class UserPreferencesResponse(BaseModel):
    id: UserPreferencesId
    user_id: UserId
    target_roles: list[str] = []
    target_locations: list[str] = []
    remote_preference: RemoteWorkPreference | None = None
    excluded_companies: list[str] = []
    min_salary: int | None = None
    salary_currency: str | None = None
    updated_at: datetime


class UpdateUserPreferencesRequest(BaseModel):
    target_roles: list[str] = []
    target_locations: list[str] = []
    remote_preference: RemoteWorkPreference | None = None
    excluded_companies: list[str] = []
    min_salary: int | None = None
    salary_currency: str | None = None


__all__ = [
    "CreateUserRequest",
    "UpdateUserPreferencesRequest",
    "UserPreferencesResponse",
    "UserResponse",
]
