"""Outreach Service route handlers.

Owned endpoints (docs/architecture/api-contracts.md#outreach-service):
    GET  /outreach
    GET  /outreach/{outreach_id}
    POST /outreach/{outreach_id}/approve
    POST /outreach/{outreach_id}/reject
    POST /outreach/{outreach_id}/edit

This is where CLAUDE.md's "external outreach requires human approval" rule
becomes a real, enforced state transition
(state-machines.md#outreach-lifecycle): `/approve` is the *only* place
`outreach.approved` is ever published, and it only runs on a row still at
`PENDING_APPROVAL` or `EDITED` — the send worker
(`outreach.consumers.handle_outreach_approved`) never runs off anything
else.

Caller-ownership: `GET /outreach` now derives `user_id` from the
authenticated token (`infrastructure.auth`) rather than a client-supplied
query param. `approve`/`reject`/`edit` do NOT yet check that the caller
actually owns the specific `outreach_id` they're acting on — any
authenticated caller can act on any id, same as before this pass. Real
per-resource ownership enforcement (not just "is this caller authenticated
at all") is explicitly deferred to a follow-up. `decided_by` is set to
`Outreach.user_id` (the row's own owner), not the caller's token, pending
that follow-up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from infrastructure.auth import CurrentUserIdDependency
from infrastructure.logging import format_context, get_logger
from outreach.api.dependencies import get_outreach_repository
from outreach.events import publish_outreach_approved
from outreach.repository import OutreachRepository
from shared.errors.codes import ErrorCode
from shared.events.payloads import OutreachDecision
from shared.types.api.outreach import (
    ApproveOutreachRequest,
    EditOutreachRequest,
    OutreachResponse,
)
from shared.types.domain.outreach import Outreach
from shared.types.enums import OutreachDecisionType, OutreachStatus
from shared.types.ids import OutreachId

router = APIRouter(tags=["outreach"])
logger = get_logger(__name__)

OutreachRepositoryDep = Annotated[OutreachRepository, Depends(get_outreach_repository)]

# Both PENDING_APPROVAL and EDITED rows may be approved or rejected —
# state-machines.md#outreach-lifecycle's diagram.
_APPROVABLE_STATUSES = frozenset({OutreachStatus.PENDING_APPROVAL, OutreachStatus.EDITED})


def _response(outreach: Outreach) -> OutreachResponse:
    return OutreachResponse(
        id=outreach.id,
        job_id=outreach.job_id,
        contact_id=outreach.contact_id,
        channel=outreach.channel,
        draft_message=outreach.draft_message,
        final_message=outreach.final_message,
        status=outreach.status,
        generated_at=outreach.generated_at,
        decided_at=outreach.decided_at,
        sent_at=outreach.sent_at,
    )


def _not_found(outreach_id: object) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": ErrorCode.NOT_FOUND.value,
            "message": f"no Outreach found with id {outreach_id}",
        },
    )


def _conflict(outreach: Outreach) -> HTTPException:
    logger.warning(
        "Outreach action rejected: 409 conflict | %s",
        format_context(outreach_id=outreach.id, status=outreach.status.value),
    )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": ErrorCode.VALIDATION_ERROR.value,
            "message": (
                f"outreach {outreach.id} is in status {outreach.status.value}, "
                "not eligible for this action"
            ),
        },
    )


@router.get("/outreach", response_model=list[OutreachResponse])
async def list_outreach(
    user_id: CurrentUserIdDependency,
    repository: OutreachRepositoryDep,
    status: OutreachStatus | None = None,
) -> list[OutreachResponse]:
    records = await repository.list_for_user(user_id, status)
    return [_response(record) for record in records]


@router.get("/outreach/{outreach_id}", response_model=OutreachResponse)
async def get_outreach(outreach_id: UUID, repository: OutreachRepositoryDep) -> OutreachResponse:
    outreach = await repository.get(OutreachId(outreach_id))
    if outreach is None:
        raise _not_found(outreach_id)
    return _response(outreach)


@router.post("/outreach/{outreach_id}/approve", response_model=OutreachResponse)
async def approve_outreach(
    outreach_id: UUID,
    request: ApproveOutreachRequest,
    repository: OutreachRepositoryDep,
) -> OutreachResponse:
    outreach = await repository.get(OutreachId(outreach_id))
    if outreach is None:
        raise _not_found(outreach_id)
    if outreach.status not in _APPROVABLE_STATUSES:
        raise _conflict(outreach)

    now = datetime.now(UTC)
    final_message = (
        request.final_message if request.final_message is not None else outreach.final_message
    )
    outreach.status = OutreachStatus.APPROVED
    outreach.final_message = final_message
    outreach.decided_at = now
    outreach.decided_by = outreach.user_id
    await repository.update(outreach)

    decision = OutreachDecision(
        outreach_id=outreach.id,
        job_id=outreach.job_id,
        user_id=outreach.user_id,
        decision=OutreachDecisionType.APPROVED,
        final_message=outreach.final_message,
        decided_at=now,
        decided_by=outreach.user_id,
    )
    # New causal-chain origin — no inbound Kafka envelope to propagate a
    # correlation_id from at an HTTP request; publish_outreach_approved's
    # default correlation_id=None mints a fresh one (see that function's
    # docstring, same "no inbound envelope" precedent Tracking Service's
    # manual PATCH endpoint uses).
    await publish_outreach_approved(decision)

    logger.info(
        "Outreach approved | %s",
        format_context(outreach_id=outreach.id, job_id=outreach.job_id),
    )
    return _response(outreach)


@router.post("/outreach/{outreach_id}/reject", response_model=OutreachResponse)
async def reject_outreach(
    outreach_id: UUID,
    repository: OutreachRepositoryDep,
) -> OutreachResponse:
    outreach = await repository.get(OutreachId(outreach_id))
    if outreach is None:
        raise _not_found(outreach_id)
    if outreach.status not in _APPROVABLE_STATUSES:
        raise _conflict(outreach)

    now = datetime.now(UTC)
    outreach.status = OutreachStatus.REJECTED
    outreach.decided_at = now
    outreach.decided_by = outreach.user_id
    await repository.update(outreach)

    logger.info(
        "Outreach rejected | %s",
        format_context(outreach_id=outreach.id, job_id=outreach.job_id),
    )
    # No event published — component-contracts.md#post-outreachoutreach_idreject's
    # documented, accepted gap: Tracking learns of a rejection only
    # indirectly, by outreach.sent never arriving for this outreach_id.
    return _response(outreach)


@router.post("/outreach/{outreach_id}/edit", response_model=OutreachResponse)
async def edit_outreach(
    outreach_id: UUID,
    request: EditOutreachRequest,
    repository: OutreachRepositoryDep,
) -> OutreachResponse:
    outreach = await repository.get(OutreachId(outreach_id))
    if outreach is None:
        raise _not_found(outreach_id)
    if outreach.status != OutreachStatus.PENDING_APPROVAL:
        raise _conflict(outreach)
    if not request.message.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": ErrorCode.VALIDATION_ERROR.value,
                "message": "edited message must not be empty",
            },
        )

    outreach.final_message = request.message
    outreach.status = OutreachStatus.EDITED
    await repository.update(outreach)

    logger.info("Outreach edited | %s", format_context(outreach_id=outreach.id))
    return _response(outreach)


__all__ = ["router"]
