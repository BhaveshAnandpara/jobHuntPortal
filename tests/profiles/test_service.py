"""Tests for profiles/service.py:ProfileService.

Runs entirely against fakes (FakeResumeRepository, FakeCandidateProfileRepository,
an LLMClient wired to ScriptedLLMProvider, a FakeProducerClient-backed
EventProducer, and a LocalResumeStorage pointed at tmp_path) — no database,
Kafka broker, or Ollama server required.
"""

from __future__ import annotations

import base64
from datetime import UTC
from uuid import uuid4

import pytest

from infrastructure.kafka.producer import EventProducer
from infrastructure.llm import LLMClient
from profiles.service import ProfileError, ProfileService
from profiles.storage import LocalResumeStorage
from shared.errors.codes import ErrorCode
from shared.types.api.profiles import CreateResumeRequest
from shared.types.enums import ProfileStatus, ResumeStatus
from shared.types.ids import ProfileId, ResumeId, UserId
from tests.profiles.conftest import (
    FakeCandidateProfileRepository,
    FakeProducerClient,
    FakeResumeRepository,
    llm_response,
    llm_response_text,
    make_llm_client,
    make_resume,
)

_FIELDS = {
    "title": "Data Analyst",
    "summary": "Turns data into decisions.",
    "skills": ["SQL", "Tableau"],
    "experience_years": 3.0,
    "seniority": "Mid",
    "education": [],
    "certifications": [],
    "projects": [],
    "industries": ["Analytics"],
    "target_roles": ["Data Analyst"],
}


def _service(
    resume_repository: FakeResumeRepository,
    profile_repository: FakeCandidateProfileRepository,
    event_producer: EventProducer,
    resume_storage: LocalResumeStorage,
    *,
    llm_client: LLMClient | None = None,
) -> ProfileService:
    return ProfileService(
        resume_repository,
        profile_repository,
        producer=event_producer,
        storage=resume_storage,
        llm_client=llm_client,
    )


# ---------------------------------------------------------------------------
# upload_resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_resume_success(
    resume_repository: FakeResumeRepository,
    profile_repository: FakeCandidateProfileRepository,
    event_producer: EventProducer,
    resume_storage: LocalResumeStorage,
) -> None:
    service = _service(resume_repository, profile_repository, event_producer, resume_storage)
    request = CreateResumeRequest(
        user_id=UserId(uuid4()),
        file_name="resume.txt",
        file_content=base64.b64encode(b"hello world").decode("ascii"),
    )

    resume = await service.upload_resume(request)

    assert resume.status == ResumeStatus.PARSING
    assert resume.file_name == "resume.txt"
    stored = resume_repository.by_id[resume.id]
    assert stored.status == ResumeStatus.PARSING
    # the file was actually written to storage, base64-decoded back to the
    # original bytes (not the base64 string itself, and not UTF-8-encoded
    # base64 text — see the CreateResumeRequest.file_content defect fix).
    assert resume_storage.read(resume.storage_uri) == b"hello world"


@pytest.mark.asyncio
async def test_upload_resume_rejects_unsupported_extension(
    resume_repository: FakeResumeRepository,
    profile_repository: FakeCandidateProfileRepository,
    event_producer: EventProducer,
    resume_storage: LocalResumeStorage,
) -> None:
    service = _service(resume_repository, profile_repository, event_producer, resume_storage)
    request = CreateResumeRequest(
        user_id=UserId(uuid4()),
        file_name="resume.exe",
        file_content=base64.b64encode(b"hello").decode("ascii"),
    )

    with pytest.raises(ProfileError) as excinfo:
        await service.upload_resume(request)
    assert excinfo.value.code == ErrorCode.VALIDATION_ERROR
    assert resume_repository.by_id == {}


@pytest.mark.asyncio
async def test_upload_resume_rejects_oversized_file(
    resume_repository: FakeResumeRepository,
    profile_repository: FakeCandidateProfileRepository,
    event_producer: EventProducer,
    resume_storage: LocalResumeStorage,
) -> None:
    service = _service(resume_repository, profile_repository, event_producer, resume_storage)
    request = CreateResumeRequest(
        user_id=UserId(uuid4()),
        file_name="resume.txt",
        file_content=base64.b64encode(b"x" * (10 * 1024 * 1024 + 1)).decode("ascii"),
    )

    with pytest.raises(ProfileError) as excinfo:
        await service.upload_resume(request)
    assert excinfo.value.code == ErrorCode.VALIDATION_ERROR


@pytest.mark.asyncio
async def test_upload_resume_rejects_empty_file(
    resume_repository: FakeResumeRepository,
    profile_repository: FakeCandidateProfileRepository,
    event_producer: EventProducer,
    resume_storage: LocalResumeStorage,
) -> None:
    service = _service(resume_repository, profile_repository, event_producer, resume_storage)
    request = CreateResumeRequest(user_id=UserId(uuid4()), file_name="resume.txt", file_content="")

    with pytest.raises(ProfileError) as excinfo:
        await service.upload_resume(request)
    assert excinfo.value.code == ErrorCode.VALIDATION_ERROR


@pytest.mark.asyncio
async def test_upload_resume_rejects_malformed_base64(
    resume_repository: FakeResumeRepository,
    profile_repository: FakeCandidateProfileRepository,
    event_producer: EventProducer,
    resume_storage: LocalResumeStorage,
) -> None:
    """Regression test for the CreateResumeRequest.file_content defect:
    malformed base64 must be rejected with a normalized VALIDATION_ERROR,
    not silently mis-decoded or stored.
    """
    service = _service(resume_repository, profile_repository, event_producer, resume_storage)
    request = CreateResumeRequest(
        user_id=UserId(uuid4()), file_name="resume.txt", file_content="not valid base64!!!"
    )

    with pytest.raises(ProfileError) as excinfo:
        await service.upload_resume(request)
    assert excinfo.value.code == ErrorCode.VALIDATION_ERROR
    assert resume_repository.by_id == {}


# ---------------------------------------------------------------------------
# run_parsing_workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_parsing_workflow_success_persists_and_publishes(
    resume_repository: FakeResumeRepository,
    profile_repository: FakeCandidateProfileRepository,
    event_producer: EventProducer,
    fake_producer_client: FakeProducerClient,
    resume_storage: LocalResumeStorage,
) -> None:
    user_id = UserId(uuid4())
    resume = make_resume(user_id=user_id, status=ResumeStatus.PARSING)
    storage_uri = resume_storage.save(user_id, resume.id, "resume.txt", b"analyst resume text")
    resume = resume.model_copy(update={"storage_uri": storage_uri})
    resume_repository.by_id[resume.id] = resume

    client, _ = make_llm_client([llm_response(_FIELDS)])
    service = _service(
        resume_repository, profile_repository, event_producer, resume_storage, llm_client=client
    )

    profile = await service.run_parsing_workflow(resume.id)

    assert profile is not None
    assert profile.status == ProfileStatus.ACTIVE
    assert profile.title == _FIELDS["title"]

    updated_resume = resume_repository.by_id[resume.id]
    assert updated_resume.status == ResumeStatus.PARSED
    assert updated_resume.raw_text == "analyst resume text"
    assert profile_repository.by_id[profile.id] == profile

    assert len(fake_producer_client.produced) == 1
    _, value, _ = fake_producer_client.produced[0]
    assert b"CREATED" in value


@pytest.mark.asyncio
async def test_run_parsing_workflow_failure_marks_parse_failed_no_event(
    resume_repository: FakeResumeRepository,
    profile_repository: FakeCandidateProfileRepository,
    event_producer: EventProducer,
    fake_producer_client: FakeProducerClient,
    resume_storage: LocalResumeStorage,
) -> None:
    user_id = UserId(uuid4())
    resume = make_resume(user_id=user_id, status=ResumeStatus.PARSING)
    storage_uri = resume_storage.save(user_id, resume.id, "resume.txt", b"unparseable")
    resume = resume.model_copy(update={"storage_uri": storage_uri})
    resume_repository.by_id[resume.id] = resume

    client, _ = make_llm_client(
        [llm_response_text("not json"), llm_response_text("still not json")]
    )
    service = _service(
        resume_repository, profile_repository, event_producer, resume_storage, llm_client=client
    )

    result = await service.run_parsing_workflow(resume.id)

    assert result is None
    updated_resume = resume_repository.by_id[resume.id]
    assert updated_resume.status == ResumeStatus.PARSE_FAILED
    assert updated_resume.parse_error is not None
    assert profile_repository.by_id == {}
    assert fake_producer_client.produced == []


@pytest.mark.asyncio
async def test_run_parsing_workflow_without_llm_provider_marks_parse_failed(
    resume_repository: FakeResumeRepository,
    profile_repository: FakeCandidateProfileRepository,
    event_producer: EventProducer,
    resume_storage: LocalResumeStorage,
) -> None:
    resume = make_resume(status=ResumeStatus.PARSING)
    resume_repository.by_id[resume.id] = resume
    service = _service(
        resume_repository, profile_repository, event_producer, resume_storage, llm_client=None
    )

    result = await service.run_parsing_workflow(resume.id)

    assert result is None
    assert resume_repository.by_id[resume.id].status == ResumeStatus.PARSE_FAILED


@pytest.mark.asyncio
async def test_run_parsing_workflow_missing_resume_returns_none(
    resume_repository: FakeResumeRepository,
    profile_repository: FakeCandidateProfileRepository,
    event_producer: EventProducer,
    resume_storage: LocalResumeStorage,
) -> None:
    service = _service(resume_repository, profile_repository, event_producer, resume_storage)
    result = await service.run_parsing_workflow(ResumeId(uuid4()))
    assert result is None


# ---------------------------------------------------------------------------
# list_resumes / delete_resume / list_profiles / get_profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_resumes_scoped_to_user(
    resume_repository: FakeResumeRepository,
    profile_repository: FakeCandidateProfileRepository,
    event_producer: EventProducer,
    resume_storage: LocalResumeStorage,
) -> None:
    user_id = UserId(uuid4())
    other_user_id = UserId(uuid4())
    mine = make_resume(user_id=user_id)
    other = make_resume(user_id=other_user_id)
    resume_repository.by_id[mine.id] = mine
    resume_repository.by_id[other.id] = other

    service = _service(resume_repository, profile_repository, event_producer, resume_storage)
    result = await service.list_resumes(user_id)

    assert [r.id for r in result] == [mine.id]


@pytest.mark.asyncio
async def test_delete_resume_not_found_raises(
    resume_repository: FakeResumeRepository,
    profile_repository: FakeCandidateProfileRepository,
    event_producer: EventProducer,
    resume_storage: LocalResumeStorage,
) -> None:
    service = _service(resume_repository, profile_repository, event_producer, resume_storage)
    with pytest.raises(ProfileError) as excinfo:
        await service.delete_resume(ResumeId(uuid4()))
    assert excinfo.value.code == ErrorCode.NOT_FOUND


@pytest.mark.asyncio
async def test_delete_resume_archives_resume_and_profile_and_publishes(
    resume_repository: FakeResumeRepository,
    profile_repository: FakeCandidateProfileRepository,
    event_producer: EventProducer,
    fake_producer_client: FakeProducerClient,
    resume_storage: LocalResumeStorage,
) -> None:
    resume = make_resume(status=ResumeStatus.PARSED)
    resume_repository.by_id[resume.id] = resume

    from datetime import datetime

    from shared.types.domain.candidate_profile import CandidateProfile

    profile = CandidateProfile(
        id=ProfileId(uuid4()),
        user_id=resume.user_id,
        resume_id=resume.id,
        title="Data Analyst",
        status=ProfileStatus.ACTIVE,
        generated_at=datetime.now(UTC),
    )
    profile_repository.by_id[profile.id] = profile

    service = _service(resume_repository, profile_repository, event_producer, resume_storage)
    await service.delete_resume(resume.id)

    assert resume_repository.by_id[resume.id].status == ResumeStatus.ARCHIVED
    assert profile_repository.by_id[profile.id].status == ProfileStatus.ARCHIVED

    assert len(fake_producer_client.produced) == 1
    _, value, _ = fake_producer_client.produced[0]
    assert b"ARCHIVED" in value


@pytest.mark.asyncio
async def test_delete_resume_without_profile_does_not_publish(
    resume_repository: FakeResumeRepository,
    profile_repository: FakeCandidateProfileRepository,
    event_producer: EventProducer,
    fake_producer_client: FakeProducerClient,
    resume_storage: LocalResumeStorage,
) -> None:
    resume = make_resume(status=ResumeStatus.PARSE_FAILED)
    resume_repository.by_id[resume.id] = resume

    service = _service(resume_repository, profile_repository, event_producer, resume_storage)
    await service.delete_resume(resume.id)

    assert resume_repository.by_id[resume.id].status == ResumeStatus.ARCHIVED
    assert fake_producer_client.produced == []


@pytest.mark.asyncio
async def test_get_profile_not_found_raises(
    resume_repository: FakeResumeRepository,
    profile_repository: FakeCandidateProfileRepository,
    event_producer: EventProducer,
    resume_storage: LocalResumeStorage,
) -> None:
    service = _service(resume_repository, profile_repository, event_producer, resume_storage)
    with pytest.raises(ProfileError) as excinfo:
        await service.get_profile(ProfileId(uuid4()))
    assert excinfo.value.code == ErrorCode.NOT_FOUND
