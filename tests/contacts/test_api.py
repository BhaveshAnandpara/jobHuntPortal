"""API tests for `GET /jobs/{job_id}/contacts` and
`POST /jobs/{job_id}/contacts/search` (FastAPI TestClient). Dependencies are
overridden with in-memory fakes — no live database/Kafka broker required.
Mirrors `tests/matching/test_api.py`'s pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from contacts.api.dependencies import get_session
from contacts.api.routes import router
from contacts.events import get_event_producer
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize
from infrastructure.kafka.topics import Topic
from shared.types.domain.contact import Contact
from shared.types.enums import ContactStatus, ContactType
from shared.types.ids import ContactId, JobId, UserId


def _contact(job_id: JobId, **overrides: object) -> Contact:
    fields: dict[str, object] = {
        "id": ContactId(uuid4()),
        "job_id": job_id,
        "user_id": UserId(uuid4()),
        "full_name": "Jordan Smith",
        "headline": "Mechanical Design Engineer",
        "company": "Acme Robotics",
        "contact_type": ContactType.PRACTITIONER,
        "profile_url": None,
        "email": None,
        "status": ContactStatus.RANKED,
        "discovered_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return Contact(**fields)


def _client(session_factory=None, broker: InMemoryBroker | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def fake_session():
        if session_factory is None:
            yield None
            return
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[get_event_producer] = lambda: EventProducer(
        "contact-discovery-service",
        client=InMemoryProducerClient(broker or InMemoryBroker()),
    )
    return TestClient(app)


@pytest.mark.asyncio
async def test_get_job_contacts_returns_persisted_contacts_with_relevance(
    session_factory,
) -> None:
    from contacts.repository import ContactRepository, ContactScoreRepository
    from shared.types.domain.contact_score import ContactScore
    from shared.types.ids import ContactScoreId

    job_id = JobId(uuid4())
    contact = _contact(job_id)
    async with session_factory() as session:
        await ContactRepository(session).add(contact)
        await ContactScoreRepository(session).add(
            ContactScore(
                id=ContactScoreId(uuid4()),
                contact_id=contact.id,
                job_id=job_id,
                relevance_score=8.75,
                same_company=True,
                department_relevance=0.8,
                role_similarity=0.9,
                seniority_fit=0.7,
                ranked_at=datetime.now(UTC),
            )
        )
        await session.commit()

    client = _client(session_factory)
    response = client.get(f"/jobs/{job_id}/contacts")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == str(contact.id)
    assert body[0]["full_name"] == "Jordan Smith"
    assert body[0]["relevance_score"] == 8.75
    assert body[0]["status"] == "RANKED"


@pytest.mark.asyncio
async def test_get_job_contacts_returns_empty_list_for_unknown_job(session_factory) -> None:
    """No 404 — see contacts/api/routes.py's documented gap: this service
    cannot distinguish "unknown job" from "no contacts found yet"."""
    client = _client(session_factory)

    response = client.get(f"/jobs/{uuid4()}/contacts")

    assert response.status_code == 200
    assert response.json() == []


def test_trigger_contact_search_publishes_contacts_requested_event() -> None:
    broker = InMemoryBroker()
    client = _client(broker=broker)
    job_id = uuid4()

    response = client.post(
        f"/jobs/{job_id}/contacts/search",
        json={
            "user_id": str(uuid4()),
            "company": "Acme Robotics",
            "title": "Mechanical Design Engineer",
            "location": "Remote",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == str(job_id)
    assert "requested_at" in body

    messages = broker.log(Topic.CONTACTS_REQUESTED.value)
    assert len(messages) == 1
    published = deserialize(Topic.CONTACTS_REQUESTED, messages[0].value())
    assert str(published.payload.job_id) == str(job_id)
    assert published.payload.company == "Acme Robotics"
    assert published.payload.title == "Mechanical Design Engineer"


def test_trigger_contact_search_rejects_missing_required_fields() -> None:
    client = _client()

    response = client.post(f"/jobs/{uuid4()}/contacts/search", json={})

    assert response.status_code == 422
