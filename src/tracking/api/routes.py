"""Tracking Service route handlers.

Owned endpoints (docs/architecture/api-contracts.md#tracking-service):
    GET /applications
    GET /applications/{application_id}
    PATCH /applications/{application_id}/status
    GET /applications/{application_id}/history
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from infrastructure.kafka.serialization import new_correlation_id
from infrastructure.logging import format_context, get_logger
from shared.errors.codes import ErrorCode
from shared.types.api.tracking import (
    ApplicationHistoryResponse,
    ApplicationResponse,
    UpdateApplicationStatusRequest,
)
from shared.types.domain.application import Application
from shared.types.domain.application_history import ApplicationHistory
from shared.types.dto import ApplicationStatusUpdate
from shared.types.enums import ApplicationStatus
from shared.types.ids import ApplicationHistoryId, ApplicationId, UserId
from tracking.api.dependencies import (
    get_application_history_repository,
    get_application_repository,
)
from tracking.events import publish_application_updated
from tracking.repository import ApplicationHistoryRepository, ApplicationRepository

router = APIRouter(tags=["tracking"])
logger = get_logger(__name__)

ApplicationRepositoryDep = Annotated[ApplicationRepository, Depends(get_application_repository)]
ApplicationHistoryRepositoryDep = Annotated[
    ApplicationHistoryRepository, Depends(get_application_history_repository)
]

# ---------------------------------------------------------------------------
# Manual transition graph (state-machines.md#opportunity-lifecycle) —
# implement exactly, reject everything else. This is deliberately a
# separate, stricter mechanism from tracking.consumers._STATUS_RANK's
# forward-progress check: a manual PATCH must validate a specific edge
# (e.g. reject DISCOVERED -> INTERVIEW even though it is "forward" by
# rank), not merely "did the rank increase".
# ---------------------------------------------------------------------------

_MANUAL_TRANSITIONS: dict[ApplicationStatus, frozenset[ApplicationStatus]] = {
    # DISCOVERED, MATCHED -> APPLIED: "user applies before the automated
    # pipeline finishes" (state-machines.md's additional-edges table).
    ApplicationStatus.DISCOVERED: frozenset(
        {ApplicationStatus.APPLIED, ApplicationStatus.WITHDRAWN}
    ),
    # MATCHED -> IGNORED: the primary lifecycle diagram's "or manual
    # dismiss (BORDERLINE)" annotation on the MATCHED -> IGNORED arrow —
    # a user may dismiss a borderline match by hand, in addition to the
    # automatic jobs.matched-consumed(recommendation=IGNORE) path (which
    # goes through tracking.consumers, not this endpoint).
    ApplicationStatus.MATCHED: frozenset(
        {
            ApplicationStatus.APPLIED,
            ApplicationStatus.WITHDRAWN,
            ApplicationStatus.IGNORED,
        }
    ),
    # SHORTLISTED..REFERRED: every state may be skipped in favor of APPLIED
    # (state-machines.md's skippability rule), or withdrawn.
    ApplicationStatus.SHORTLISTED: frozenset(
        {ApplicationStatus.APPLIED, ApplicationStatus.WITHDRAWN}
    ),
    ApplicationStatus.CONTACT_SEARCH: frozenset(
        {ApplicationStatus.APPLIED, ApplicationStatus.WITHDRAWN}
    ),
    ApplicationStatus.CONTACT_FOUND: frozenset(
        {ApplicationStatus.APPLIED, ApplicationStatus.WITHDRAWN}
    ),
    ApplicationStatus.OUTREACH_GENERATED: frozenset(
        {ApplicationStatus.APPLIED, ApplicationStatus.WITHDRAWN}
    ),
    ApplicationStatus.OUTREACH_APPROVED: frozenset(
        {ApplicationStatus.APPLIED, ApplicationStatus.WITHDRAWN}
    ),
    # OUTREACH_SENT -> REFERRED: "manual (user confirms a referral
    # occurred)" — the one primary-chain manual step before REFERRED.
    ApplicationStatus.OUTREACH_SENT: frozenset(
        {
            ApplicationStatus.REFERRED,
            ApplicationStatus.APPLIED,
            ApplicationStatus.WITHDRAWN,
        }
    ),
    ApplicationStatus.REFERRED: frozenset(
        {ApplicationStatus.APPLIED, ApplicationStatus.WITHDRAWN}
    ),
    # APPLIED -> INTERVIEW (primary chain, manual) or -> REJECTED
    # ("rejection without interview", additional-edges table).
    ApplicationStatus.APPLIED: frozenset(
        {
            ApplicationStatus.INTERVIEW,
            ApplicationStatus.REJECTED,
            ApplicationStatus.WITHDRAWN,
        }
    ),
    # INTERVIEW -> OFFER | REJECTED (primary chain, manual).
    ApplicationStatus.INTERVIEW: frozenset(
        {
            ApplicationStatus.OFFER,
            ApplicationStatus.REJECTED,
            ApplicationStatus.WITHDRAWN,
        }
    ),
    # Terminal states: no outgoing edges — any PATCH out of these is
    # rejected below.
    ApplicationStatus.OFFER: frozenset(),
    ApplicationStatus.REJECTED: frozenset(),
    ApplicationStatus.IGNORED: frozenset(),
    ApplicationStatus.WITHDRAWN: frozenset(),
}
"""Every status not present as a key here (CONTACT_SEARCH etc. are present;
this covers all 15 ApplicationStatus members) would be a programming error
— all are listed explicitly for that reason rather than relying on
`dict.get(..., frozenset())` to paper over a missing entry."""


def _not_found(application_id: object) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": ErrorCode.NOT_FOUND.value,
            "message": f"no Application found with id {application_id}",
        },
    )


def _application_response(application: Application) -> ApplicationResponse:
    return ApplicationResponse(
        id=application.id,
        job_id=application.job_id,
        user_id=application.user_id,
        company=application.company,
        title=application.title,
        status=application.status,
        selected_resume_id=application.selected_resume_id,
        match_score=application.match_score,
        matched_skills=application.matched_skills,
        missing_skills=application.missing_skills,
        referral_contact_id=application.referral_contact_id,
        applied_date=application.applied_date,
        follow_up_date=application.follow_up_date,
        notes=application.notes,
        created_at=application.created_at,
        updated_at=application.updated_at,
    )


def _history_response(entry: ApplicationHistory) -> ApplicationHistoryResponse:
    return ApplicationHistoryResponse(
        id=entry.id,
        application_id=entry.application_id,
        from_status=entry.from_status,
        to_status=entry.to_status,
        changed_at=entry.changed_at,
        triggered_by=entry.triggered_by,
        source_event_type=entry.source_event_type,
        correlation_id=entry.correlation_id,
    )


@router.get("/applications", response_model=list[ApplicationResponse])
async def list_applications(
    user_id: UUID,
    repository: ApplicationRepositoryDep,
    status: ApplicationStatus | None = None,
) -> list[ApplicationResponse]:
    applications = await repository.list_for_user(UserId(user_id), status)
    return [_application_response(application) for application in applications]


@router.get("/applications/{application_id}", response_model=ApplicationResponse)
async def get_application(
    application_id: UUID,
    repository: ApplicationRepositoryDep,
) -> ApplicationResponse:
    application = await repository.get(ApplicationId(application_id))
    if application is None:
        raise _not_found(application_id)
    return _application_response(application)


@router.patch("/applications/{application_id}/status", response_model=ApplicationResponse)
async def update_application_status(
    application_id: UUID,
    request: UpdateApplicationStatusRequest,
    repository: ApplicationRepositoryDep,
    history_repository: ApplicationHistoryRepositoryDep,
) -> ApplicationResponse:
    application = await repository.get(ApplicationId(application_id))
    if application is None:
        raise _not_found(application_id)

    allowed = _MANUAL_TRANSITIONS.get(application.status, frozenset())
    if request.new_status not in allowed:
        logger.warning(
            "Invalid application status transition attempted | %s",
            format_context(
                application_id=application_id,
                from_status=application.status.value,
                to_status=request.new_status.value,
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": (
                    f"cannot transition Application {application_id} from "
                    f"{application.status.value} to {request.new_status.value}"
                ),
            },
        )

    now = datetime.now(UTC)
    previous_status = application.status
    application.status = request.new_status
    if request.new_status == ApplicationStatus.APPLIED:
        application.applied_date = request.applied_date or now.date()
    if request.notes is not None:
        application.notes = request.notes
    application.updated_at = now

    await repository.update(application)

    correlation_id = new_correlation_id()
    await history_repository.add(
        ApplicationHistory(
            id=ApplicationHistoryId(uuid4()),
            application_id=application.id,
            from_status=previous_status,
            to_status=request.new_status,
            changed_at=now,
            triggered_by="user",
            source_event_type=None,
            correlation_id=correlation_id,
        )
    )

    status_update = ApplicationStatusUpdate(
        application_id=application.id,
        job_id=application.job_id,
        user_id=application.user_id,
        previous_status=previous_status,
        new_status=request.new_status,
        changed_at=now,
        triggered_by="user",
    )
    await publish_application_updated(status_update, correlation_id=correlation_id)

    logger.info(
        "Application status transition | %s",
        format_context(
            application_id=application.id,
            from_status=previous_status.value,
            to_status=request.new_status.value,
            triggered_by="user",
        ),
    )
    return _application_response(application)


@router.get(
    "/applications/{application_id}/history",
    response_model=list[ApplicationHistoryResponse],
)
async def get_application_history(
    application_id: UUID,
    repository: ApplicationRepositoryDep,
    history_repository: ApplicationHistoryRepositoryDep,
) -> list[ApplicationHistoryResponse]:
    application = await repository.get(ApplicationId(application_id))
    if application is None:
        raise _not_found(application_id)
    entries = await history_repository.list_for_application(application.id)
    return [_history_response(entry) for entry in entries]


__all__ = ["router"]
