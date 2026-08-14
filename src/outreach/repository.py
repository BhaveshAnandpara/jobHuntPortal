"""Persistence boundary for Outreach Service's owned table (`outreach`).
Implements the shared.contracts.Repository interface.

Repositories take and return the canonical domain type
(`shared.types.domain.outreach.Outreach`); the `OutreachRecord` SQLAlchemy
model in models.py never leaves this module — mirrors
`matching/repository.py`'s / `contacts/repository.py`'s `_to_domain`-style
projection pattern.

No other component may import this module — the human approval action is
always mediated through this service's own API.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.models import OutreachRecord
from shared.types.domain.outreach import Outreach
from shared.types.enums import OutreachStatus
from shared.types.ids import ContactId, JobId, OutreachId, UserId


def _to_domain(record: OutreachRecord) -> Outreach:
    return Outreach(
        id=OutreachId(record.id),
        job_id=JobId(record.job_id),
        contact_id=ContactId(record.contact_id),
        user_id=UserId(record.user_id),
        channel=record.channel,
        draft_message=record.draft_message,
        final_message=record.final_message,
        status=record.status,
        generated_at=record.generated_at,
        decided_at=record.decided_at,
        decided_by=UserId(record.decided_by) if record.decided_by is not None else None,
        sent_at=record.sent_at,
        external_message_id=record.external_message_id,
        send_error=record.send_error,
    )


class OutreachRepository:
    """Reads/writes only `outreach`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, outreach_id: OutreachId) -> Outreach | None:
        record = await self._session.get(OutreachRecord, UUID(str(outreach_id)))
        return _to_domain(record) if record is not None else None

    async def get_for_job(self, job_id: JobId) -> Outreach | None:
        """Most recent (in practice: only) `Outreach` row for `job_id`, or
        `None` if none exists yet. Used by `outreach.consumers`'
        `contacts.found` idempotency check — a redelivered `contacts.found`
        for a `job_id` that already produced an `Outreach` row must not
        generate a second draft (see consumers.py's docstring).
        """
        result = await self._session.scalars(
            select(OutreachRecord)
            .where(OutreachRecord.job_id == UUID(str(job_id)))
            .order_by(OutreachRecord.generated_at.desc())
            .limit(1)
        )
        record = result.first()
        return _to_domain(record) if record is not None else None

    async def list_for_user(
        self, user_id: UserId, status: OutreachStatus | None = None
    ) -> list[Outreach]:
        """`GET /outreach` — api-contracts.md#outreach-service."""
        stmt = select(OutreachRecord).where(OutreachRecord.user_id == UUID(str(user_id)))
        if status is not None:
            stmt = stmt.where(OutreachRecord.status == status)
        result = await self._session.scalars(stmt.order_by(OutreachRecord.generated_at.desc()))
        return [_to_domain(record) for record in result.all()]

    async def add(self, outreach: Outreach, *, recipient_address: str | None = None) -> Outreach:
        """`recipient_address` is persistence-layer-only bookkeeping, never
        part of the `Outreach` domain type — see models.py's
        `OutreachRecord.recipient_address` docstring for the full
        rationale (same precedent as `job_matches.published_at`).
        """
        self._session.add(
            OutreachRecord(
                id=outreach.id,
                job_id=outreach.job_id,
                contact_id=outreach.contact_id,
                user_id=outreach.user_id,
                channel=outreach.channel,
                draft_message=outreach.draft_message,
                final_message=outreach.final_message,
                status=outreach.status,
                generated_at=outreach.generated_at,
                decided_at=outreach.decided_at,
                decided_by=outreach.decided_by,
                sent_at=outreach.sent_at,
                external_message_id=outreach.external_message_id,
                send_error=outreach.send_error,
                recipient_address=recipient_address,
            )
        )
        await self._session.flush()
        return outreach

    async def get_recipient_address(self, outreach_id: OutreachId) -> str | None:
        """Read-back for `OutreachRecord.recipient_address` — see that
        column's docstring. Used only by the send-worker consumer to build
        `OutboundMessage.recipient`; never exposed via `_to_domain`.
        """
        record = await self._session.get(OutreachRecord, UUID(str(outreach_id)))
        return record.recipient_address if record is not None else None

    async def update(self, outreach: Outreach) -> Outreach:
        record = await self._session.get(OutreachRecord, UUID(str(outreach.id)))
        if record is None:
            raise ValueError(f"no Outreach record with id {outreach.id}")
        record.channel = outreach.channel
        record.draft_message = outreach.draft_message
        record.final_message = outreach.final_message
        record.status = outreach.status
        record.generated_at = outreach.generated_at
        record.decided_at = outreach.decided_at
        record.decided_by = outreach.decided_by
        record.sent_at = outreach.sent_at
        record.external_message_id = outreach.external_message_id
        record.send_error = outreach.send_error
        await self._session.flush()
        return outreach


__all__ = ["OutreachRepository"]
