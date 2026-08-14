"""API tests for Tracking Service's four owned endpoints (FastAPI
TestClient). Uses a real SQLite in-memory session (via the `session_factory`
fixture) bound in place of `tracking.api.dependencies.get_session`, and the
Kafka infra's in-memory broker for `tracking.events.publish_application_updated`
— so every PATCH's DB write and event publish are exercised for real, not
faked out, matching this component's emphasis on the manual-transition
graph and the resulting `applications.updated` event.

Scenario letters (see task brief / tests/tracking/test_consumers.py's
module docstring for the full list):
    E - invalid transition -> VALIDATION_ERROR, no DB write, no event
    F - direct-application-path skips succeed
    G - INTERVIEW -> OFFER (manual)
    H - INTERVIEW -> REJECTED (manual)
    I - history ordering via GET .../history
    J - applications.updated correctness (manual-API-triggered half)
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import tracking.events as events_module
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize
from infrastructure.kafka.topics import Topic
from shared.types.domain.application import Application
from shared.types.enums import ApplicationStatus
from shared.types.ids import ApplicationId, JobId, UserId
from tracking.api.dependencies import get_session
from tracking.api.routes import router
from tracking.repository import ApplicationRepository


def _application(**overrides: object) -> Application:
    now = datetime.now(UTC)
    fields: dict[str, object] = {
        "id": ApplicationId(uuid4()),
        "job_id": JobId(uuid4()),
        "user_id": UserId(uuid4()),
        "company": "Acme Robotics",
        "title": "Senior Mechanical Design Engineer",
        "status": ApplicationStatus.DISCOVERED,
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    return Application(**fields)


def _client(session_factory: async_sessionmaker[AsyncSession]) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def override_get_session():
        async with session_factory() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


async def _seed(session_factory: async_sessionmaker[AsyncSession], application: Application) -> None:
    async with session_factory() as session:
        await ApplicationRepository(session).add(application)
        await session.commit()


@pytest.fixture(autouse=True)
def _wire_broker() -> InMemoryBroker:
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("tracking-service", client=InMemoryProducerClient(broker))
    )
    return broker


# ---------------------------------------------------------------------------
# GET /applications, GET /applications/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_application_returns_200_with_body(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = _application()
    await _seed(session_factory, application)
    client = _client(session_factory)

    response = client.get(f"/applications/{application.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(application.id)
    assert body["status"] == "DISCOVERED"
    assert body["company"] == "Acme Robotics"


def test_get_application_returns_404_when_missing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = _client(session_factory)

    response = client.get(f"/applications/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_list_applications_filters_by_user_and_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    discovered = _application(user_id=user_id, status=ApplicationStatus.DISCOVERED)
    matched = _application(user_id=user_id, status=ApplicationStatus.MATCHED)
    other_user = _application(status=ApplicationStatus.DISCOVERED)
    await _seed(session_factory, discovered)
    await _seed(session_factory, matched)
    await _seed(session_factory, other_user)
    client = _client(session_factory)

    response = client.get("/applications", params={"user_id": str(user_id)})
    assert response.status_code == 200
    ids = {row["id"] for row in response.json()}
    assert ids == {str(discovered.id), str(matched.id)}

    response = client.get(
        "/applications", params={"user_id": str(user_id), "status": "MATCHED"}
    )
    assert response.status_code == 200
    ids = {row["id"] for row in response.json()}
    assert ids == {str(matched.id)}


# ---------------------------------------------------------------------------
# Scenario E — invalid transition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_status_rejects_invalid_transition(
    session_factory: async_sessionmaker[AsyncSession], _wire_broker: InMemoryBroker
) -> None:
    application = _application(status=ApplicationStatus.DISCOVERED)
    await _seed(session_factory, application)
    client = _client(session_factory)

    response = client.patch(
        f"/applications/{application.id}/status", json={"new_status": "INTERVIEW"}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"

    # No DB write.
    async with session_factory() as session:
        stored = await ApplicationRepository(session).get(application.id)
        assert stored.status == ApplicationStatus.DISCOVERED

    # No event published.
    assert _wire_broker.log(Topic.APPLICATIONS_UPDATED.value) == []


@pytest.mark.asyncio
async def test_patch_status_rejects_transition_out_of_terminal_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = _application(status=ApplicationStatus.OFFER)
    await _seed(session_factory, application)
    client = _client(session_factory)

    response = client.patch(
        f"/applications/{application.id}/status", json={"new_status": "APPLIED"}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_patch_status_returns_404_when_missing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = _client(session_factory)

    response = client.patch(
        f"/applications/{uuid4()}/status", json={"new_status": "APPLIED"}
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Scenario F — direct application path (skip table)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "starting_status",
    [
        ApplicationStatus.DISCOVERED,
        ApplicationStatus.MATCHED,
        ApplicationStatus.SHORTLISTED,
        ApplicationStatus.CONTACT_SEARCH,
        ApplicationStatus.CONTACT_FOUND,
        ApplicationStatus.OUTREACH_GENERATED,
        ApplicationStatus.OUTREACH_APPROVED,
        ApplicationStatus.OUTREACH_SENT,
        ApplicationStatus.REFERRED,
    ],
)
async def test_patch_status_allows_direct_application_from_any_pre_applied_state(
    session_factory: async_sessionmaker[AsyncSession], starting_status: ApplicationStatus
) -> None:
    application = _application(status=starting_status)
    await _seed(session_factory, application)
    client = _client(session_factory)

    response = client.patch(
        f"/applications/{application.id}/status",
        json={"new_status": "APPLIED", "applied_date": "2026-08-13", "notes": "applied directly"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "APPLIED"
    assert body["applied_date"] == "2026-08-13"
    assert body["notes"] == "applied directly"


@pytest.mark.asyncio
async def test_patch_status_applied_defaults_applied_date_when_not_given(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = _application(status=ApplicationStatus.DISCOVERED)
    await _seed(session_factory, application)
    client = _client(session_factory)

    response = client.patch(
        f"/applications/{application.id}/status", json={"new_status": "APPLIED"}
    )

    assert response.status_code == 200
    assert response.json()["applied_date"] == datetime.now(UTC).date().isoformat()


# ---------------------------------------------------------------------------
# Scenario G / H — INTERVIEW -> OFFER / REJECTED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_status_interview_to_offer(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = _application(status=ApplicationStatus.INTERVIEW)
    await _seed(session_factory, application)
    client = _client(session_factory)

    response = client.patch(
        f"/applications/{application.id}/status", json={"new_status": "OFFER"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "OFFER"


@pytest.mark.asyncio
async def test_patch_status_interview_to_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = _application(status=ApplicationStatus.INTERVIEW)
    await _seed(session_factory, application)
    client = _client(session_factory)

    response = client.patch(
        f"/applications/{application.id}/status", json={"new_status": "REJECTED"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_patch_status_applied_to_rejected_without_interview(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = _application(status=ApplicationStatus.APPLIED)
    await _seed(session_factory, application)
    client = _client(session_factory)

    response = client.patch(
        f"/applications/{application.id}/status", json={"new_status": "REJECTED"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_patch_status_withdrawn_from_any_non_terminal_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = _application(status=ApplicationStatus.CONTACT_FOUND)
    await _seed(session_factory, application)
    client = _client(session_factory)

    response = client.patch(
        f"/applications/{application.id}/status", json={"new_status": "WITHDRAWN"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "WITHDRAWN"


@pytest.mark.asyncio
async def test_patch_status_matched_to_ignored_manual_dismiss(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = _application(status=ApplicationStatus.MATCHED)
    await _seed(session_factory, application)
    client = _client(session_factory)

    response = client.patch(
        f"/applications/{application.id}/status", json={"new_status": "IGNORED"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "IGNORED"


# ---------------------------------------------------------------------------
# Scenario I — history ordering via the API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_application_history_returns_chronological_order(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    application = _application(status=ApplicationStatus.APPLIED)
    await _seed(session_factory, application)
    client = _client(session_factory)

    r1 = client.patch(f"/applications/{application.id}/status", json={"new_status": "INTERVIEW"})
    assert r1.status_code == 200
    r2 = client.patch(f"/applications/{application.id}/status", json={"new_status": "OFFER"})
    assert r2.status_code == 200

    response = client.get(f"/applications/{application.id}/history")
    assert response.status_code == 200
    body = response.json()
    assert [row["to_status"] for row in body] == ["INTERVIEW", "OFFER"]
    assert body[0]["from_status"] == "APPLIED"
    assert body[1]["from_status"] == "INTERVIEW"
    # chronological, not reverse-chronological
    changed_at = [row["changed_at"] for row in body]
    assert changed_at == sorted(changed_at)


def test_get_application_history_returns_404_when_missing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client = _client(session_factory)

    response = client.get(f"/applications/{uuid4()}/history")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Scenario J — applications.updated correctness (manual-API-triggered half)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_status_publishes_applications_updated_with_correct_fields(
    session_factory: async_sessionmaker[AsyncSession], _wire_broker: InMemoryBroker
) -> None:
    application = _application(status=ApplicationStatus.APPLIED)
    await _seed(session_factory, application)
    client = _client(session_factory)

    response = client.patch(
        f"/applications/{application.id}/status", json={"new_status": "INTERVIEW"}
    )
    assert response.status_code == 200

    messages = _wire_broker.log(Topic.APPLICATIONS_UPDATED.value)
    assert len(messages) == 1
    published = deserialize(Topic.APPLICATIONS_UPDATED, messages[0].value())
    assert published.payload.application_id == application.id
    assert published.payload.previous_status == ApplicationStatus.APPLIED
    assert published.payload.new_status == ApplicationStatus.INTERVIEW
    assert published.payload.triggered_by == "user"
    # Manual PATCH has no inbound envelope to propagate from — a fresh
    # correlation_id is minted (tracking/api/routes.py).
    assert published.correlation_id is not None
