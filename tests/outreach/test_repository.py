"""Tests for `outreach.repository.OutreachRepository` against a real
(SQLite in-memory) session — mirrors `tests/matching/test_repository.py`'s
/ `tests/contacts/test_repository.py`'s pattern.

Also covers Scenario K (persistence): correct `Outreach` state at each
stage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from outreach.repository import OutreachRepository
from shared.types.domain.outreach import Outreach
from shared.types.enums import OutreachChannel, OutreachStatus
from shared.types.ids import ContactId, JobId, OutreachId, UserId

pytestmark = pytest.mark.asyncio


def _outreach(**overrides: object) -> Outreach:
    fields: dict[str, object] = {
        "id": OutreachId(uuid4()),
        "job_id": JobId(uuid4()),
        "contact_id": ContactId(uuid4()),
        "user_id": UserId(uuid4()),
        "channel": OutreachChannel.LINKEDIN_CONNECTION_REQUEST,
        "draft_message": "Hi Jordan, I'd love to connect about the role at Acme.",
        "final_message": None,
        "status": OutreachStatus.PENDING_APPROVAL,
        "generated_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return Outreach(**fields)


async def test_add_and_get_round_trip(session_factory) -> None:
    outreach = _outreach()
    async with session_factory() as session:
        await OutreachRepository(session).add(outreach)
        await session.commit()

    async with session_factory() as session:
        stored = await OutreachRepository(session).get(outreach.id)

    assert stored is not None
    assert stored.id == outreach.id
    assert stored.status == OutreachStatus.PENDING_APPROVAL
    assert stored.draft_message == outreach.draft_message


async def test_get_returns_none_for_unknown_id(session_factory) -> None:
    async with session_factory() as session:
        stored = await OutreachRepository(session).get(OutreachId(uuid4()))
    assert stored is None


async def test_get_for_job_finds_row_by_job_id(session_factory) -> None:
    job_id = JobId(uuid4())
    outreach = _outreach(job_id=job_id)
    async with session_factory() as session:
        await OutreachRepository(session).add(outreach)
        await session.commit()

    async with session_factory() as session:
        stored = await OutreachRepository(session).get_for_job(job_id)

    assert stored is not None
    assert stored.job_id == job_id


async def test_get_for_job_returns_none_when_no_row_exists(session_factory) -> None:
    async with session_factory() as session:
        stored = await OutreachRepository(session).get_for_job(JobId(uuid4()))
    assert stored is None


async def test_list_for_user_filters_by_status(session_factory) -> None:
    user_id = UserId(uuid4())
    pending = _outreach(user_id=user_id, status=OutreachStatus.PENDING_APPROVAL)
    approved = _outreach(user_id=user_id, status=OutreachStatus.APPROVED)
    other_user = _outreach(status=OutreachStatus.PENDING_APPROVAL)

    async with session_factory() as session:
        repo = OutreachRepository(session)
        await repo.add(pending)
        await repo.add(approved)
        await repo.add(other_user)
        await session.commit()

    async with session_factory() as session:
        all_for_user = await OutreachRepository(session).list_for_user(user_id)
        only_pending = await OutreachRepository(session).list_for_user(
            user_id, OutreachStatus.PENDING_APPROVAL
        )

    assert {o.id for o in all_for_user} == {pending.id, approved.id}
    assert [o.id for o in only_pending] == [pending.id]


async def test_update_persists_status_transitions_pending_to_approved_to_sent(
    session_factory,
) -> None:
    """Scenario K: correct Outreach state at each stage of the lifecycle."""
    outreach = _outreach(status=OutreachStatus.PENDING_APPROVAL)
    async with session_factory() as session:
        await OutreachRepository(session).add(outreach)
        await session.commit()

    now = datetime.now(UTC)
    outreach.status = OutreachStatus.APPROVED
    outreach.decided_at = now
    outreach.decided_by = outreach.user_id
    async with session_factory() as session:
        await OutreachRepository(session).update(outreach)
        await session.commit()

    async with session_factory() as session:
        after_approve = await OutreachRepository(session).get(outreach.id)
    assert after_approve.status == OutreachStatus.APPROVED
    assert after_approve.decided_at is not None

    outreach.status = OutreachStatus.SENT
    outreach.sent_at = now
    outreach.external_message_id = "ext-123"
    async with session_factory() as session:
        await OutreachRepository(session).update(outreach)
        await session.commit()

    async with session_factory() as session:
        after_send = await OutreachRepository(session).get(outreach.id)
    assert after_send.status == OutreachStatus.SENT
    assert after_send.external_message_id == "ext-123"


async def test_update_unknown_id_raises(session_factory) -> None:
    async with session_factory() as session:
        with pytest.raises(ValueError):
            await OutreachRepository(session).update(_outreach())


async def test_recipient_address_persisted_and_not_projected_onto_domain_type(
    session_factory,
) -> None:
    """`recipient_address` is persistence-layer-only bookkeeping (see
    models.py's docstring) — stored via `add(..., recipient_address=...)`,
    read back only via `get_recipient_address`, never present on the
    `Outreach` domain object itself.
    """
    outreach = _outreach()
    async with session_factory() as session:
        await OutreachRepository(session).add(
            outreach, recipient_address="https://example.com/in/jordan-smith"
        )
        await session.commit()

    async with session_factory() as session:
        stored = await OutreachRepository(session).get(outreach.id)
        address = await OutreachRepository(session).get_recipient_address(outreach.id)

    assert not hasattr(stored, "recipient_address")
    assert address == "https://example.com/in/jordan-smith"
