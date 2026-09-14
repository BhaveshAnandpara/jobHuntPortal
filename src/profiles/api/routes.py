"""Resume/Profile Service route handlers.

Owned endpoints (docs/architecture/api-contracts.md#resumeprofile-service):
    POST   /resumes
    GET    /resumes
    DELETE /resumes/{resume_id}
    GET    /profiles
    GET    /profiles/{profile_id}
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from infrastructure.auth import CurrentUserIdDependency
from infrastructure.llm import LlmNotConfiguredError, get_user_llm_client
from profiles.api.dependencies import get_profile_service
from profiles.service import ProfileError, ProfileService
from shared.errors.codes import ErrorCode
from shared.types.api.profiles import CreateResumeRequest, ResumeResponse
from shared.types.domain.resume import Resume
from shared.types.dto import ResumeProfile
from shared.types.ids import ProfileId, ResumeId

router = APIRouter(tags=["profiles"])

ProfileServiceDep = Annotated[ProfileService, Depends(get_profile_service)]

_STATUS_BY_ERROR_CODE = {
    ErrorCode.VALIDATION_ERROR: status.HTTP_400_BAD_REQUEST,
    ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCode.LLM_NOT_CONFIGURED: status.HTTP_400_BAD_REQUEST,
}


def _http_error(error: ProfileError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_BY_ERROR_CODE[error.code],
        detail={"code": error.code.value, "message": error.message},
    )


LLM_NOT_CONFIGURED_MESSAGE = "Add your AI provider key in Settings to use this feature."
"""Rendered verbatim by the frontend — keep it a complete, actionable sentence."""


async def require_llm_configured(user_id: CurrentUserIdDependency) -> None:
    """Reject the request unless the caller has a validated LLM config.

    Declared as a route-level `dependencies=[...]` entry rather than checked
    inside the handler, because FastAPI inserts those at the front of the
    dependency list and solves them in order: the gate therefore runs before
    `get_profile_service` resolves a session, before any file is written to
    storage, and before any `profiles.*` event is produced — which is the
    whole point of "blocked immediately, never discovered deep inside a
    pipeline" (docs/llm-provider-config/PRODUCT.md).

    `get_user_llm_client` is the sanctioned seam for asking this question:
    the `user_llm_config` row (exists *and* `last_validated_at is not None`)
    is User Service's private state, and the architecture audit forbids this
    component from importing `users.repository` to read it directly. The
    call costs one indexed row read and a local decrypt — no network call is
    made, since every provider in this layer connects only inside
    `.complete()`.
    """
    try:
        await get_user_llm_client(user_id)
    except LlmNotConfiguredError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": ErrorCode.LLM_NOT_CONFIGURED.value,
                "message": LLM_NOT_CONFIGURED_MESSAGE,
            },
        ) from error


def _resume_response(resume: Resume) -> ResumeResponse:
    return ResumeResponse(
        id=resume.id,
        user_id=resume.user_id,
        file_name=resume.file_name,
        status=resume.status,
        uploaded_at=resume.uploaded_at,
    )


@router.post(
    "/resumes",
    response_model=ResumeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_llm_configured)],
)
async def upload_resume(
    request: CreateResumeRequest,
    user_id: CurrentUserIdDependency,
    background_tasks: BackgroundTasks,
    service: ProfileServiceDep,
) -> ResumeResponse:
    try:
        resume = await service.upload_resume(request, user_id)
    except ProfileError as error:
        raise _http_error(error) from error

    # Parsing is asynchronous relative to the upload request (per
    # component-contracts.md#post-resumes-upload) — the caller gets a
    # Resume id (status=PARSING) immediately, and parsing completes in the
    # background, on this same injected `service` (safe: FastAPI runs a
    # yield dependency's teardown, e.g. the DB commit, after background
    # tasks — see profiles/api/dependencies.py:get_session).
    background_tasks.add_task(service.run_parsing_workflow, resume.id)
    return _resume_response(resume)


@router.get("/resumes", response_model=list[ResumeResponse])
async def list_resumes(
    user_id: CurrentUserIdDependency,
    service: ProfileServiceDep,
) -> list[ResumeResponse]:
    resumes = await service.list_resumes(user_id)
    return [_resume_response(resume) for resume in resumes]


@router.delete("/resumes/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_resume(
    resume_id: UUID,
    service: ProfileServiceDep,
) -> None:
    try:
        await service.delete_resume(ResumeId(resume_id))
    except ProfileError as error:
        raise _http_error(error) from error


@router.get("/profiles", response_model=list[ResumeProfile])
async def list_profiles(
    user_id: CurrentUserIdDependency,
    service: ProfileServiceDep,
) -> list[ResumeProfile]:
    return await service.list_profiles(user_id)


@router.get("/profiles/{profile_id}", response_model=ResumeProfile)
async def get_profile(
    profile_id: UUID,
    service: ProfileServiceDep,
) -> ResumeProfile:
    try:
        return await service.get_profile(ProfileId(profile_id))
    except ProfileError as error:
        raise _http_error(error) from error


__all__ = ["LLM_NOT_CONFIGURED_MESSAGE", "require_llm_configured", "router"]
