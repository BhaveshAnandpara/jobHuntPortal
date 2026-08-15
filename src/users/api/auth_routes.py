"""Authentication route handlers — separate from `users/api/routes.py`
(registration/preferences CRUD) since this is credential-check-shaped
logic, not resource CRUD. Owned endpoint:
    POST /auth/login

Mounted into the app in api/main.py alongside `users.api.routes.router`.
"""

from fastapi import APIRouter, HTTPException, status

from infrastructure.auth import create_access_token
from shared.errors.codes import ErrorCode
from shared.types.api.auth import LoginRequest, LoginResponse
from shared.types.api.users import UserResponse
from shared.types.domain.user import User
from users.api.routes import UserServiceDependency
from users.service import UserError

router = APIRouter(prefix="/auth", tags=["auth"])

_STATUS_BY_ERROR_CODE = {
    ErrorCode.VALIDATION_ERROR: status.HTTP_400_BAD_REQUEST,
    ErrorCode.UNAUTHORIZED: status.HTTP_401_UNAUTHORIZED,
}


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at,
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    service: UserServiceDependency,
) -> LoginResponse:
    try:
        user = await service.login(request)
    except UserError as error:
        raise HTTPException(
            status_code=_STATUS_BY_ERROR_CODE[error.code],
            detail={"code": error.code.value, "message": error.message},
        ) from error

    return LoginResponse(access_token=create_access_token(user.id), user=_user_response(user))


__all__ = ["router"]
