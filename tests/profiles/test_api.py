"""API tests for Resume/Profile Service (profiles/api/routes.py), covering
all five endpoints per docs/architecture/api-contracts.md#resumeprofile-service.

Uses a standalone FastAPI app (not api/main.py, which mounts every other
component's router too — several of those don't exist yet in this parallel
Wave 1 pass) with `get_profile_service` overridden to return a
`ProfileService` built entirely from fakes. No database, Kafka broker, or
Ollama server required.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from profiles.api.dependencies import get_profile_service
from profiles.api.routes import router
from profiles.service import ProfileService
from profiles.storage import LocalResumeStorage
from shared.types.domain.candidate_profile import CandidateProfile
from shared.types.enums import ProfileStatus, ResumeStatus
from shared.types.ids import ProfileId, UserId
from tests.profiles.conftest import (
    FakeCandidateProfileRepository,
    FakeResumeRepository,
    llm_response,
    make_llm_client,
    make_resume,
)

_FIELDS = {
    "title": "Product Manager",
    "summary": "Ships products end to end.",
    "skills": ["Roadmapping", "SQL"],
    "experience_years": 4.0,
    "seniority": "Senior",
    "education": [],
    "certifications": [],
    "projects": [],
    "industries": ["Product"],
    "target_roles": ["Product Manager"],
}


@pytest.fixture
def app_and_deps(event_producer, resume_storage):
    resumes = FakeResumeRepository()
    profiles = FakeCandidateProfileRepository()
    llm_client, _ = make_llm_client([llm_response(_FIELDS)])

    service = ProfileService(
        resumes,
        profiles,
        producer=event_producer,
        storage=resume_storage,
        llm_client=llm_client,
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_profile_service] = lambda: service

    return app, resumes, profiles


@pytest.fixture
def client(app_and_deps):
    app, _, _ = app_and_deps
    return TestClient(app)


def test_post_resumes_returns_202_and_runs_parsing(app_and_deps, client: TestClient) -> None:
    _, resumes, profiles = app_and_deps
    user_id = str(uuid4())

    response = client.post(
        "/resumes",
        json={"user_id": user_id, "file_name": "resume.txt", "file_content": "aGVsbG8gd29ybGQ="},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["user_id"] == user_id
    assert body["file_name"] == "resume.txt"
    # TestClient runs the request synchronously, including scheduled
    # BackgroundTasks, so parsing has already completed by the time we get
    # the response back.
    assert len(resumes.by_id) == 1
    assert len(profiles.by_id) == 1
    (resume,) = resumes.by_id.values()
    assert resume.status == ResumeStatus.PARSED


def test_post_resumes_validation_error_returns_400(client: TestClient) -> None:
    response = client.post(
        "/resumes",
        json={"user_id": str(uuid4()), "file_name": "resume.exe", "file_content": "aGVsbG8="},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_post_resumes_recovers_original_bytes_end_to_end(
    app_and_deps, client: TestClient, resume_storage: LocalResumeStorage
) -> None:
    """Regression test for the CreateResumeRequest.file_content defect:
    plain Pydantic `bytes` does not base64-decode a JSON string, so real
    resume content was never correctly extracted in real (non-fake-LLM)
    usage. This posts a base64-encoded resume through the actual `/resumes`
    endpoint (real endpoint, real request model — not a shortcut) and
    asserts the original bytes/text are recovered on the other end, both at
    the storage layer and after text extraction.

    This test fails against the pre-fix implementation
    (`CreateResumeRequest.file_content: bytes`), because the stored bytes
    would be the UTF-8 encoding of the base64 *string itself*
    (`b"SmFuZSBEb2U..."`), not the decoded original resume bytes.
    """
    _, resumes, _ = app_and_deps
    user_id = str(uuid4())
    original_text = "Jane Doe\nMechanical Design Engineer\nSolidWorks\nGD&T"
    original_bytes = original_text.encode("utf-8")
    encoded = base64.b64encode(original_bytes).decode("ascii")

    response = client.post(
        "/resumes",
        json={"user_id": user_id, "file_name": "resume.txt", "file_content": encoded},
    )

    assert response.status_code == 202
    (resume,) = resumes.by_id.values()
    # storage layer: exact original bytes, not the base64 string re-encoded
    assert resume_storage.read(resume.storage_uri) == original_bytes
    # TestClient runs BackgroundTasks synchronously, so parsing has already
    # completed: raw_text is the real extracted resume content, not the
    # base64 wire string and not mis-decoded garbage.
    assert resume.raw_text == original_text


def test_post_resumes_malformed_base64_returns_normalized_400(app_and_deps, client: TestClient) -> None:
    """Malformed base64 must produce this service's normalized
    `{"code","message"}` 400 (via ProfileError/ErrorCode.VALIDATION_ERROR),
    not a stored garbage resume and not a raw FastAPI/Pydantic traceback or
    422 array body — there is no global RequestValidationError handler to
    normalize that shape.
    """
    _, resumes, profiles = app_and_deps

    response = client.post(
        "/resumes",
        json={
            "user_id": str(uuid4()),
            "file_name": "resume.txt",
            "file_content": "not valid base64!!!",
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["code"] == "VALIDATION_ERROR"
    assert isinstance(body["detail"]["message"], str)
    assert resumes.by_id == {}
    assert profiles.by_id == {}


def test_post_resumes_llm_receives_decoded_resume_text(
    event_producer, resume_storage: LocalResumeStorage
) -> None:
    """Closes the exact test gap that let the file_content defect survive
    618 pre-existing tests: every prior test used a `ScriptedLLMProvider`
    that ignores prompt content entirely (it just pops the next scripted
    response). This test captures the actual prompt argument sent to the
    LLM client via the real `/resumes` endpoint and asserts it contains the
    real, decoded resume text — not the base64 wire string, and not
    mis-decoded garbage.
    """
    fields = {
        "title": "Mechanical Design Engineer",
        "summary": None,
        "skills": ["SolidWorks", "GD&T"],
        "experience_years": None,
        "seniority": None,
        "education": [],
        "certifications": [],
        "projects": [],
        "industries": [],
        "target_roles": [],
    }
    llm_client, provider = make_llm_client([llm_response(fields)])
    service = ProfileService(
        FakeResumeRepository(),
        FakeCandidateProfileRepository(),
        producer=event_producer,
        storage=resume_storage,
        llm_client=llm_client,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_profile_service] = lambda: service
    client = TestClient(app)

    original_text = "Jane Doe\nMechanical Design Engineer\nSolidWorks\nGD&T"
    encoded = base64.b64encode(original_text.encode("utf-8")).decode("ascii")

    response = client.post(
        "/resumes",
        json={"user_id": str(uuid4()), "file_name": "resume.txt", "file_content": encoded},
    )

    assert response.status_code == 202
    assert len(provider.requests) == 1
    prompt = provider.requests[0].prompt
    assert original_text in prompt
    assert encoded not in prompt


def test_get_resumes_lists_for_user(app_and_deps, client: TestClient) -> None:
    _, resumes, _ = app_and_deps
    user_id = UserId(uuid4())
    resume = make_resume(user_id=user_id)
    resumes.by_id[resume.id] = resume

    response = client.get("/resumes", params={"user_id": str(user_id)})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == str(resume.id)


def test_delete_resume_returns_204(app_and_deps, client: TestClient) -> None:
    _, resumes, _ = app_and_deps
    resume = make_resume()
    resumes.by_id[resume.id] = resume

    response = client.delete(f"/resumes/{resume.id}")

    assert response.status_code == 204
    assert resumes.by_id[resume.id].status == ResumeStatus.ARCHIVED


def test_delete_resume_missing_returns_404(client: TestClient) -> None:
    response = client.delete(f"/resumes/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


def test_get_profiles_lists_for_user(app_and_deps, client: TestClient) -> None:
    _, _, profiles = app_and_deps
    user_id = UserId(uuid4())
    profile = CandidateProfile(
        id=ProfileId(uuid4()),
        user_id=user_id,
        resume_id=uuid4(),
        title="Product Manager",
        status=ProfileStatus.ACTIVE,
        generated_at=datetime.now(UTC),
    )
    profiles.by_id[profile.id] = profile

    response = client.get("/profiles", params={"user_id": str(user_id)})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["profile_id"] == str(profile.id)
    assert body[0]["title"] == "Product Manager"


def test_get_profile_by_id(app_and_deps, client: TestClient) -> None:
    _, _, profiles = app_and_deps
    profile = CandidateProfile(
        id=ProfileId(uuid4()),
        user_id=uuid4(),
        resume_id=uuid4(),
        title="Product Manager",
        status=ProfileStatus.ACTIVE,
        generated_at=datetime.now(UTC),
    )
    profiles.by_id[profile.id] = profile

    response = client.get(f"/profiles/{profile.id}")

    assert response.status_code == 200
    assert response.json()["profile_id"] == str(profile.id)


def test_get_profile_missing_returns_404(client: TestClient) -> None:
    response = client.get(f"/profiles/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"
