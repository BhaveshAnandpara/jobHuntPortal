"""User Service route handlers.

Owned endpoints (docs/architecture/api-contracts.md#user-service):
    POST /users                    (also mints a token — auto-login on
                                     registration, see `_login_response`)
    GET  /users/me/preferences     (identity from the bearer token, not
                                     a path param — see `POST /auth/login`
                                     in auth_routes.py for how the token
                                     is obtained)
    PUT  /users/me/preferences
    GET  /users/me/llm-config      (the user's own AI provider credentials —
                                     see docs/llm-provider-config/PRODUCT.md)
    PUT  /users/me/llm-config
    GET  /users/me/skill-prompts   (the user's own additional instructions
                                     per AI skill — see
                                     docs/prompt-customization/PRODUCT.md)
    PUT  /users/me/skill-prompts
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from infrastructure.auth import CurrentUserIdDependency, create_access_token
from shared.errors.codes import ErrorCode
from shared.types.api.auth import LoginResponse
from shared.types.api.users import (
    CreateUserRequest,
    SkillPromptsResponse,
    UpdateSkillPromptsRequest,
    UpdateUserLlmConfigRequest,
    UpdateUserPreferencesRequest,
    UserLlmConfigResponse,
    UserPreferencesResponse,
    UserResponse,
)
from shared.types.domain.user import User
from shared.types.domain.user_llm_config import UserLlmConfig
from shared.types.domain.user_preferences import UserPreferences
from users.api.dependencies import get_user_service
from users.service import UserError, UserService

router = APIRouter(prefix="/users", tags=["users"])

UserServiceDependency = Annotated[UserService, Depends(get_user_service)]

_STATUS_BY_ERROR_CODE = {
    ErrorCode.VALIDATION_ERROR: status.HTTP_400_BAD_REQUEST,
    ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCode.UNAUTHORIZED: status.HTTP_401_UNAUTHORIZED,
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


def _login_response(user: User) -> LoginResponse:
    return LoginResponse(access_token=create_access_token(user.id), user=_user_response(user))


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


def _llm_config_response(config: UserLlmConfig) -> UserLlmConfigResponse:
    """Project the masked domain object onto the wire contract.

    Nothing here can leak a usable key: `config` is built by
    `UserLlmConfigRepository.get`, which decrypts only far enough to compute
    `key_hint` and never places the plaintext on the object it returns.
    """
    return UserLlmConfigResponse(
        provider=config.provider,
        model=config.model,
        key_hint=config.key_hint,
        last_validated_at=config.last_validated_at,
    )


@router.post("", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    request: CreateUserRequest,
    service: UserServiceDependency,
) -> LoginResponse:
    try:
        return _login_response(await service.create_user(request))
    except UserError as error:
        raise _http_error(error) from error


@router.get("/me/preferences", response_model=UserPreferencesResponse)
async def get_preferences(
    user_id: CurrentUserIdDependency,
    service: UserServiceDependency,
) -> UserPreferencesResponse:
    try:
        return _preferences_response(await service.get_preferences(user_id))
    except UserError as error:
        raise _http_error(error) from error


@router.put("/me/preferences", response_model=UserPreferencesResponse)
async def replace_preferences(
    user_id: CurrentUserIdDependency,
    request: UpdateUserPreferencesRequest,
    service: UserServiceDependency,
) -> UserPreferencesResponse:
    try:
        return _preferences_response(
            await service.replace_preferences(user_id, request)
        )
    except UserError as error:
        raise _http_error(error) from error


@router.get("/me/llm-config", response_model=UserLlmConfigResponse)
async def get_llm_config(
    user_id: CurrentUserIdDependency,
    service: UserServiceDependency,
) -> UserLlmConfigResponse:
    """Return the caller's saved AI provider configuration.

    404 when nothing is configured — the same "unset is not an error"
    convention as `GET /users/me/preferences`, which the frontend already
    handles.
    """
    try:
        return _llm_config_response(await service.get_llm_config(user_id))
    except UserError as error:
        raise _http_error(error) from error


@router.put("/me/llm-config", response_model=UserLlmConfigResponse)
async def replace_llm_config(
    user_id: CurrentUserIdDependency,
    request: UpdateUserLlmConfigRequest,
    service: UserServiceDependency,
) -> UserLlmConfigResponse:
    """Validate the submitted key live, then replace the stored config.

    Returns 200 with the same masked shape `GET` returns — never the key
    that was just submitted. A key the provider rejects comes back as
    `400 VALIDATION_ERROR` and persists nothing, so a failed save leaves any
    previously-working configuration intact.
    """
    try:
        return _llm_config_response(await service.replace_llm_config(user_id, request))
    except UserError as error:
        raise _http_error(error) from error


@router.get("/me/skill-prompts", response_model=SkillPromptsResponse)
async def get_skill_prompts(
    user_id: CurrentUserIdDependency,
    service: UserServiceDependency,
) -> SkillPromptsResponse:
    """Return the caller's own per-skill custom instructions.

    200 with `{}` when nothing is saved, rather than the 404 the sibling
    `/users/me/llm-config` and `/users/me/preferences` reads use for
    "unset" — see `SkillPromptsResponse` for why an empty map is a complete
    answer here.
    """
    try:
        return SkillPromptsResponse(instructions=await service.get_skill_prompts(user_id))
    except UserError as error:
        raise _http_error(error) from error


@router.put("/me/skill-prompts", response_model=SkillPromptsResponse)
async def replace_skill_prompts(
    user_id: CurrentUserIdDependency,
    request: UpdateSkillPromptsRequest,
    service: UserServiceDependency,
) -> SkillPromptsResponse:
    """Replace the caller's whole instructions map.

    Full replace, like every other `PUT` on this router: a skill omitted
    from the body — or submitted empty — has its customization cleared, and
    the response is the stored state, so the form can re-seed from what the
    server actually kept rather than from what it sent.

    Unknown skill keys and over-long values are rejected by
    `UpdateSkillPromptsRequest` before this handler runs (422, field-level).
    Nothing here inspects *what* the user asked for: the guarantee that an
    unsafe request has no effect is enforced where the text is used, by the
    append point's ordering and framing, not by refusing the save.
    """
    try:
        return SkillPromptsResponse(
            instructions=await service.replace_skill_prompts(user_id, request)
        )
    except UserError as error:
        raise _http_error(error) from error


__all__ = ["router"]
