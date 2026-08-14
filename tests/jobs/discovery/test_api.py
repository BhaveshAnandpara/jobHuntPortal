"""API tests for `POST`/`GET /job-sources` (FastAPI TestClient). The
repository dependency is overridden with an in-memory fake — no live
database required.
"""

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jobs.discovery.api import router
from jobs.discovery.dependencies import get_job_source_repository
from shared.types.enums import JobSourceType
from tests.jobs.conftest import FakeJobSourceRepository, make_job_source


def _client(repository: FakeJobSourceRepository | None = None) -> tuple[TestClient, FakeJobSourceRepository]:
    app = FastAPI()
    app.include_router(router)
    repository = repository if repository is not None else FakeJobSourceRepository()
    app.dependency_overrides[get_job_source_repository] = lambda: repository
    return TestClient(app), repository


def test_create_job_source_returns_201() -> None:
    client, repository = _client()
    user_id = str(uuid4())

    response = client.post(
        "/job-sources",
        json={
            "user_id": user_id,
            "name": "LinkedIn - Mechanical roles",
            "type": "LINKEDIN",
            "query_config": {"keywords": ["Mechanical Engineer"]},
            "enabled": True,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "LinkedIn - Mechanical roles"
    assert body["enabled"] is True
    assert body["last_run_at"] is None
    assert len(repository.sources) == 1


def test_create_job_source_rejects_empty_name() -> None:
    client, repository = _client()

    response = client.post(
        "/job-sources",
        json={
            "user_id": str(uuid4()),
            "name": "   ",
            "type": "LINKEDIN",
            "enabled": True,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"
    assert repository.sources == {}


def test_list_job_sources_filters_by_user() -> None:
    user_a = uuid4()
    user_b = uuid4()
    from shared.types.ids import UserId

    source_a = make_job_source(UserId(user_a), name="A source")
    source_b = make_job_source(UserId(user_b), name="B source")
    client, _repository = _client(FakeJobSourceRepository([source_a, source_b]))

    response = client.get("/job-sources", params={"user_id": str(user_a)})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "A source"


def test_list_job_sources_empty_for_unknown_user() -> None:
    client, _repository = _client()

    response = client.get("/job-sources", params={"user_id": str(uuid4())})

    assert response.status_code == 200
    assert response.json() == []


def test_create_job_source_preserves_source_type() -> None:
    client, repository = _client()

    response = client.post(
        "/job-sources",
        json={
            "user_id": str(uuid4()),
            "name": "Indeed search",
            "type": "INDEED",
            "enabled": False,
        },
    )

    assert response.status_code == 201
    stored = next(iter(repository.sources.values()))
    assert stored.type is JobSourceType.INDEED
    assert stored.enabled is False
