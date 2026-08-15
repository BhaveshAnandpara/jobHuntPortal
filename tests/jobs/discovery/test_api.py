"""API tests for `POST`/`GET /job-sources` (FastAPI TestClient). The
repository dependency is overridden with an in-memory fake — no live
database required.
"""

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrastructure.auth.dependencies import get_current_user_id
from jobs.discovery.api import router
from jobs.discovery.dependencies import get_job_source_repository
from shared.types.enums import JobSourceType
from shared.types.ids import UserId
from tests.jobs.conftest import FakeJobSourceRepository, make_job_source


def _client(
    repository: FakeJobSourceRepository | None = None, *, user_id: UserId | None = None
) -> tuple[TestClient, FakeJobSourceRepository, UserId]:
    app = FastAPI()
    app.include_router(router)
    repository = repository if repository is not None else FakeJobSourceRepository()
    user_id = user_id if user_id is not None else UserId(uuid4())
    app.dependency_overrides[get_job_source_repository] = lambda: repository
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app), repository, user_id


def test_create_job_source_returns_201() -> None:
    client, repository, user_id = _client()

    response = client.post(
        "/job-sources",
        json={
            "name": "LinkedIn - Mechanical roles",
            "type": "LINKEDIN",
            "query_config": {"keywords": ["Mechanical Engineer"]},
            "enabled": True,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user_id"] == str(user_id)  # sourced from the (overridden) token
    assert body["name"] == "LinkedIn - Mechanical roles"
    assert body["enabled"] is True
    assert body["last_run_at"] is None
    assert len(repository.sources) == 1


def test_create_job_source_rejects_empty_name() -> None:
    client, repository, _user_id = _client()

    response = client.post(
        "/job-sources",
        json={"name": "   ", "type": "LINKEDIN", "enabled": True},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"
    assert repository.sources == {}


def test_list_job_sources_filters_by_user() -> None:
    user_a = UserId(uuid4())
    user_b = UserId(uuid4())

    source_a = make_job_source(user_a, name="A source")
    source_b = make_job_source(user_b, name="B source")
    client, _repository, _user_id = _client(
        FakeJobSourceRepository([source_a, source_b]), user_id=user_a
    )

    response = client.get("/job-sources")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "A source"


def test_list_job_sources_empty_for_unknown_user() -> None:
    client, _repository, _user_id = _client()

    response = client.get("/job-sources")

    assert response.status_code == 200
    assert response.json() == []


def test_create_job_source_preserves_source_type() -> None:
    client, repository, _user_id = _client()

    response = client.post(
        "/job-sources",
        json={"name": "Indeed search", "type": "INDEED", "enabled": False},
    )

    assert response.status_code == 201
    stored = next(iter(repository.sources.values()))
    assert stored.type is JobSourceType.INDEED
    assert stored.enabled is False
