"""User Service route handlers.

Owned endpoints (docs/architecture/api-contracts.md#user-service):
    POST /users
    GET  /users/{user_id}/preferences
    PUT  /users/{user_id}/preferences
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from shared.errors.codes import ErrorCode
from shared.types.api.users import (
    CreateUserRequest,
    UpdateUserPreferencesRequest,
    UserPreferencesResponse,
    UserResponse,
)
from shared.types.domain.user import User
from shared.types.domain.user_preferences import UserPreferences
from shared.types.ids import UserId
from users.api.dependencies import get_user_service
from users.service import UserError, UserService

router = APIRouter(prefix="/users", tags=["users"])

UserServiceDependency = Annotated[UserService, Depends(get_user_service)]

_STATUS_BY_ERROR_CODE = {
    ErrorCode.VALIDATION_ERROR: status.HTTP_400_BAD_REQUEST,
    ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
}


def _http_error(error: UserError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_BY_ERROR_CODE[error.code],
        detail={"code": error.code.value, "message": error.message},
    )


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at,
    )


def _preferences_response(preferences: UserPreferences) -> UserPreferencesResponse:
    return UserPreferencesResponse(
        id=preferences.id,
        user_id=preferences.user_id,
        target_roles=preferences.target_roles,
        target_locations=preferences.target_locations,
        remote_preference=preferences.remote_preference,
        excluded_companies=preferences.excluded_companies,
        min_salary=preferences.min_salary,
        salary_currency=preferences.salary_currency,
        updated_at=preferences.updated_at,
    )


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    request: CreateUserRequest,
    service: UserServiceDependency,
) -> UserResponse:
    try:
        return _user_response(await service.create_user(request))
    except UserError as error:
        raise _http_error(error) from error


@router.get("/{user_id}/preferences", response_model=UserPreferencesResponse)
async def get_preferences(
    user_id: UUID,
    service: UserServiceDependency,
) -> UserPreferencesResponse:
    try:
        return _preferences_response(await service.get_preferences(UserId(user_id)))
    except UserError as error:
        raise _http_error(error) from error


@router.put("/{user_id}/preferences", response_model=UserPreferencesResponse)
async def replace_preferences(
    user_id: UUID,
    request: UpdateUserPreferencesRequest,
    service: UserServiceDependency,
) -> UserPreferencesResponse:
    try:
        return _preferences_response(
            await service.replace_preferences(UserId(user_id), request)
        )
    except UserError as error:
        raise _http_error(error) from error


__all__ = ["router"]
