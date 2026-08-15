"""API tests for `POST /jobs/ingest-url` (FastAPI TestClient). Every
dependency (`repository`, `page_fetcher`, `extractor`, `producer`) is
overridden with a fake/in-memory implementation — no live database, LLM,
browser, or Kafka broker required.
"""

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrastructure.auth.dependencies import get_current_user_id
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.topics import Topic
from jobs.ingestion.api import router
from jobs.ingestion.dependencies import (
    get_event_producer,
    get_job_repository,
    get_page_fetcher,
    get_structured_extractor,
)
from shared.types.ids import UserId
from tests.jobs.conftest import (
    FakeExtractor,
    FakeJobRepository,
    FakePageFetcher,
    make_extracted_fields,
    make_job,
)

URL = "https://boards.example.com/jobs/42"


def _client(
    *,
    repository: FakeJobRepository | None = None,
    pages: dict[str, str] | None = None,
    errors: dict[str, Exception] | None = None,
    extractor_by_content: dict | None = None,
    broker: InMemoryBroker | None = None,
    user_id: UserId | None = None,
) -> tuple[TestClient, FakeJobRepository, InMemoryBroker, UserId]:
    app = FastAPI()
    app.include_router(router)

    repository = repository if repository is not None else FakeJobRepository()
    fetcher = FakePageFetcher(pages=pages or {}, errors=errors or {})
    extractor = FakeExtractor(by_content=extractor_by_content or {})
    broker = broker if broker is not None else InMemoryBroker()
    producer = EventProducer("job-ingestion-service", client=InMemoryProducerClient(broker))
    user_id = user_id if user_id is not None else UserId(uuid4())

    app.dependency_overrides[get_job_repository] = lambda: repository
    app.dependency_overrides[get_page_fetcher] = lambda: fetcher
    app.dependency_overrides[get_structured_extractor] = lambda: extractor
    app.dependency_overrides[get_event_producer] = lambda: producer
    app.dependency_overrides[get_current_user_id] = lambda: user_id

    return TestClient(app), repository, broker, user_id


def test_ingest_job_url_success_returns_202() -> None:
    client, repository, broker, user_id = _client(
        pages={URL: "posting body"},
        extractor_by_content={"posting body": make_extracted_fields(company="Acme")},
    )

    response = client.post("/jobs/ingest-url", json={"url": URL})

    assert response.status_code == 202
    body = response.json()
    assert body["user_id"] == str(user_id)  # sourced from the (overridden) token, not the request
    assert body["company"] == "Acme"
    assert body["processing_status"] == "NORMALIZED"
    assert body["location"] == "Remote"
    assert body["description"]
    assert body["extracted_skills"] == ["CAD", "GD&T"]
    assert body["experience_required"] == "5+ years"
    assert body["source_url"] == URL
    assert len(repository.jobs) == 1
    assert len(broker.log(Topic.JOBS_DISCOVERED.value)) == 1


def test_ingest_job_url_requires_authentication() -> None:
    client, _repository, _broker, _user_id = _client(
        pages={URL: "posting body"},
        extractor_by_content={"posting body": make_extracted_fields()},
    )
    client.app.dependency_overrides.pop(get_current_user_id)  # no override -> real dependency

    response = client.post("/jobs/ingest-url", json={"url": URL})

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


def test_ingest_job_url_dedup_returns_same_job_and_publishes_once() -> None:
    client, repository, broker, _user_id = _client(
        pages={URL: "posting body"},
        extractor_by_content={"posting body": make_extracted_fields()},
    )
    payload = {"url": URL}

    first = client.post("/jobs/ingest-url", json=payload)
    second = client.post("/jobs/ingest-url", json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert len(repository.jobs) == 1
    assert len(broker.log(Topic.JOBS_DISCOVERED.value)) == 1


def test_ingest_job_url_invalid_url_returns_400() -> None:
    client, repository, _broker, _user_id = _client()

    response = client.post("/jobs/ingest-url", json={"url": "not-a-url"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_JOB_URL"
    assert repository.jobs == []


def test_ingest_job_url_fetch_failure_returns_400() -> None:
    client, repository, broker, _user_id = _client(errors={URL: RuntimeError("network down")})

    response = client.post("/jobs/ingest-url", json={"url": URL})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "JOB_FETCH_FAILED"
    assert repository.jobs == []
    assert broker.log(Topic.JOBS_DISCOVERED.value) == []


def test_ingest_job_url_malformed_request_returns_422() -> None:
    client, _repository, _broker, _user_id = _client()

    response = client.post("/jobs/ingest-url", json={"url": 12345})  # url must be a string

    assert response.status_code == 422


def test_get_job_returns_company_and_title() -> None:
    job = make_job(uuid4(), company="Acme Robotics", title="Senior Mechanical Engineer")
    client, _repository, _broker, _user_id = _client(repository=FakeJobRepository(jobs=[job]))

    response = client.get(f"/jobs/{job.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(job.id)
    assert body["company"] == "Acme Robotics"
    assert body["title"] == "Senior Mechanical Engineer"


def test_get_job_returns_full_posting_detail() -> None:
    """Step 10.5: GET /jobs/{job_id} must expose location/description/
    extracted_skills/experience_required/source_url — previously flagged
    as a frontend-blocking gap (docs/frontend/routes.md) since JobResponse
    didn't project these already-persisted Job fields.
    """
    job = make_job(
        uuid4(),
        location="Remote",
        description_raw="Design and validate mechanical subsystems.",
        extracted_skills=["CAD", "GD&T"],
        experience_required="5+ years",
        source_url="https://boards.example.com/jobs/1",
    )
    client, _repository, _broker, _user_id = _client(repository=FakeJobRepository(jobs=[job]))

    response = client.get(f"/jobs/{job.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["location"] == "Remote"
    assert body["description"] == "Design and validate mechanical subsystems."
    assert body["extracted_skills"] == ["CAD", "GD&T"]
    assert body["experience_required"] == "5+ years"
    assert body["source_url"] == "https://boards.example.com/jobs/1"


def test_get_job_unknown_id_returns_404() -> None:
    client, _repository, _broker, _user_id = _client()

    response = client.get(f"/jobs/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"
