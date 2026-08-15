"""API tests for `GET /outreach`, `GET /outreach/{id}`,
`POST /outreach/{id}/approve|reject|edit` (FastAPI TestClient).
Dependencies are overridden with a real SQLite in-memory session and an
in-memory Kafka broker. Mirrors `tests/contacts/test_api.py`'s /
`tests/matching/test_api.py`'s pattern.

Covers Scenario F (duplicate approval -> 409, only one outreach.approved
published) and part of Scenario D/K (approve/reject/edit transitions).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import outreach.events as events_module
from infrastructure.auth.dependencies import get_current_user_id
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize
from infrastructure.kafka.topics import Topic
from outreach.api.dependencies import get_session
from outreach.api.routes import router
from outreach.repository import OutreachRepository
from shared.types.domain.outreach import Outreach
from shared.types.enums import OutreachChannel, OutreachStatus
from shared.types.ids import ContactId, JobId, OutreachId, UserId


def _outreach(**overrides: object) -> Outreach:
    fields: dict[str, object] = {
        "id": OutreachId(uuid4()),
        "job_id": JobId(uuid4()),
        "contact_id": ContactId(uuid4()),
        "user_id": UserId(uuid4()),
        "channel": OutreachChannel.LINKEDIN_CONNECTION_REQUEST,
        "draft_message": "Hi Jordan, I'd love to connect about the role.",
        "final_message": None,
        "status": OutreachStatus.PENDING_APPROVAL,
        "generated_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return Outreach(**fields)


def _client(
    session_factory=None, broker: InMemoryBroker | None = None, user_id: UserId | None = None
) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def fake_session():
        async with session_factory() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[get_current_user_id] = lambda: user_id or UserId(uuid4())
    # outreach.api.routes.approve_outreach calls outreach.events
    # .publish_outreach_approved directly (a module-level singleton), not
    # a FastAPI-injected dependency — there is no `get_event_producer`
    # dependency to override in this router (unlike Contact Discovery's
    # `POST /jobs/{job_id}/contacts/search`, which does use one). The test
    # seam is `events_module.set_event_producer`, reset automatically by
    # tests/outreach/conftest.py's autouse fixture after each test.
    events_module.set_event_producer(
        EventProducer("outreach-service", client=InMemoryProducerClient(broker or InMemoryBroker()))
    )
    return TestClient(app)


async def _seed(session_factory, outreach: Outreach) -> None:
    async with session_factory() as session:
        await OutreachRepository(session).add(outreach)
        await session.commit()


@pytest.mark.asyncio
async def test_get_outreach_returns_200_with_body(session_factory) -> None:
    outreach = _outreach()
    await _seed(session_factory, outreach)
    client = _client(session_factory)

    response = client.get(f"/outreach/{outreach.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(outreach.id)
    assert body["status"] == "PENDING_APPROVAL"
    assert body["draft_message"] == outreach.draft_message


def test_get_outreach_returns_404_when_missing(session_factory) -> None:
    client = _client(session_factory)
    response = client.get(f"/outreach/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_list_outreach_filters_by_user_and_status(session_factory) -> None:
    user_id = UserId(uuid4())
    pending = _outreach(user_id=user_id, status=OutreachStatus.PENDING_APPROVAL)
    approved = _outreach(user_id=user_id, status=OutreachStatus.APPROVED)
    other_user = _outreach(status=OutreachStatus.PENDING_APPROVAL)
    await _seed(session_factory, pending)
    await _seed(session_factory, approved)
    await _seed(session_factory, other_user)
    client = _client(session_factory, user_id=user_id)

    response = client.get("/outreach")
    assert response.status_code == 200
    assert {row["id"] for row in response.json()} == {str(pending.id), str(approved.id)}

    filtered = client.get("/outreach", params={"status": "PENDING_APPROVAL"})
    assert filtered.status_code == 200
    assert [row["id"] for row in filtered.json()] == [str(pending.id)]


@pytest.mark.asyncio
async def test_approve_outreach_transitions_to_approved_and_publishes_event(
    session_factory,
) -> None:
    outreach = _outreach(status=OutreachStatus.PENDING_APPROVAL)
    await _seed(session_factory, outreach)
    broker = InMemoryBroker()
    client = _client(session_factory, broker=broker)

    response = client.post(f"/outreach/{outreach.id}/approve", json={"final_message": None})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "APPROVED"

    messages = broker.log(Topic.OUTREACH_APPROVED.value)
    assert len(messages) == 1
    published = deserialize(Topic.OUTREACH_APPROVED, messages[0].value())
    assert published.payload.outreach_id == outreach.id
    assert published.payload.decision == "APPROVED"


@pytest.mark.asyncio
async def test_approve_outreach_with_edited_final_message(session_factory) -> None:
    outreach = _outreach(status=OutreachStatus.EDITED, final_message="Edited version")
    await _seed(session_factory, outreach)
    client = _client(session_factory)

    response = client.post(f"/outreach/{outreach.id}/approve", json={})

    assert response.status_code == 200
    assert response.json()["final_message"] == "Edited version"


def test_approve_outreach_returns_404_when_missing(session_factory) -> None:
    client = _client(session_factory)
    response = client.post(f"/outreach/{uuid4()}/approve", json={})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_approval_returns_409_and_publishes_only_once(session_factory) -> None:
    """Scenario F: calling /approve twice on an already-APPROVED row ->
    second call rejected with 409, only one outreach.approved published."""
    outreach = _outreach(status=OutreachStatus.PENDING_APPROVAL)
    await _seed(session_factory, outreach)
    broker = InMemoryBroker()
    client = _client(session_factory, broker=broker)

    first = client.post(f"/outreach/{outreach.id}/approve", json={})
    assert first.status_code == 200

    second = client.post(f"/outreach/{outreach.id}/approve", json={})
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "VALIDATION_ERROR"

    assert len(broker.log(Topic.OUTREACH_APPROVED.value)) == 1


@pytest.mark.asyncio
async def test_reject_outreach_transitions_to_rejected_and_publishes_nothing(
    session_factory,
) -> None:
    outreach = _outreach(status=OutreachStatus.PENDING_APPROVAL)
    await _seed(session_factory, outreach)
    broker = InMemoryBroker()
    client = _client(session_factory, broker=broker)

    response = client.post(f"/outreach/{outreach.id}/reject")

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"
    assert broker.log(Topic.OUTREACH_APPROVED.value) == []


@pytest.mark.asyncio
async def test_reject_already_rejected_returns_409(session_factory) -> None:
    outreach = _outreach(status=OutreachStatus.REJECTED)
    await _seed(session_factory, outreach)
    client = _client(session_factory)

    response = client.post(f"/outreach/{outreach.id}/reject")

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_edit_outreach_transitions_to_edited(session_factory) -> None:
    outreach = _outreach(status=OutreachStatus.PENDING_APPROVAL)
    await _seed(session_factory, outreach)
    client = _client(session_factory)

    response = client.post(f"/outreach/{outreach.id}/edit", json={"message": "A better draft"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "EDITED"
    assert body["final_message"] == "A better draft"


@pytest.mark.asyncio
async def test_edit_outreach_rejects_empty_message(session_factory) -> None:
    outreach = _outreach(status=OutreachStatus.PENDING_APPROVAL)
    await _seed(session_factory, outreach)
    client = _client(session_factory)

    response = client.post(f"/outreach/{outreach.id}/edit", json={"message": "   "})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_edit_outreach_not_pending_approval_returns_409(session_factory) -> None:
    outreach = _outreach(status=OutreachStatus.APPROVED)
    await _seed(session_factory, outreach)
    client = _client(session_factory)

    response = client.post(f"/outreach/{outreach.id}/edit", json={"message": "x"})

    assert response.status_code == 409
