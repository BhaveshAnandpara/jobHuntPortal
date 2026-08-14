"""Fixtures and fakes for Resume/Profile Service tests.

Everything here runs against fakes or SQLite in-memory — no PostgreSQL,
Kafka broker, or Ollama server is required to run this package.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from infrastructure.kafka.client import Headers
from infrastructure.kafka.producer import EventProducer
from infrastructure.llm import LLMClient, LLMConfig
from infrastructure.llm.provider import LLMRequest, LLMResponse
from profiles.storage import LocalResumeStorage
from shared.types.domain.candidate_profile import CandidateProfile
from shared.types.domain.resume import Resume
from shared.types.enums import ProfileStatus, ResumeStatus
from shared.types.ids import ProfileId, ResumeId, UserId

# ---------------------------------------------------------------------------
# Fake LLMProvider — satisfies infrastructure.llm.provider.LLMProvider
# structurally, no live Ollama server required.
# ---------------------------------------------------------------------------


class ScriptedLLMProvider:
    """Each call to `.complete()` pops the next scripted item: an
    `LLMResponse` to return, or an `Exception` instance to raise. Every
    request actually sent is recorded on `.requests` so tests can assert on
    prompt content (e.g. profession-independence).
    """

    name = "fake-llm"

    def __init__(self, script: list[LLMResponse | Exception]) -> None:
        self._script = list(script)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._script:
            raise AssertionError("ScriptedLLMProvider ran out of scripted responses")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def llm_response(fields: dict[str, Any], *, model: str = "fake-model") -> LLMResponse:
    """An `LLMResponse` whose text is the JSON encoding of `fields` — the
    shape `parse_structured` expects for `ExtractedResumeProfile`.
    """
    return LLMResponse(text=json.dumps(fields), model=model)


def llm_response_text(text: str, *, model: str = "fake-model") -> LLMResponse:
    """An `LLMResponse` carrying arbitrary (possibly malformed) text, for
    exercising the structured-output repair path.
    """
    return LLMResponse(text=text, model=model)


def make_llm_client(
    script: list[LLMResponse | Exception], *, config: LLMConfig | None = None
) -> tuple[LLMClient, ScriptedLLMProvider]:
    """Build a real `infrastructure.llm.LLMClient` wired to a
    `ScriptedLLMProvider` — exercises the actual retry/repair orchestration
    in infrastructure/llm/structured.py, with no live Ollama server. Returns
    `(client, provider)` so tests can still assert on `provider.requests`.

    `retry_backoff_seconds` defaults to 0 regardless of what `config` (or
    its absence) would otherwise resolve to, so scripted transport-retry
    tests don't actually sleep.
    """
    provider = ScriptedLLMProvider(script)
    cfg = (config or LLMConfig()).model_copy(update={"retry_backoff_seconds": 0.0})
    return LLMClient(provider=provider, config=cfg), provider


# ---------------------------------------------------------------------------
# Fake Kafka ProducerClient — satisfies
# infrastructure.kafka.client.ProducerClient structurally, no live broker
# required.
# ---------------------------------------------------------------------------


class FakeProducerClient:
    def __init__(self) -> None:
        self.produced: list[tuple[str, bytes | None, bytes | None]] = []

    def produce(
        self,
        topic: str,
        value: bytes | None = None,
        key: bytes | None = None,
        headers: Headers | None = None,
    ) -> None:
        self.produced.append((topic, value, key))

    def poll(self, timeout: float) -> int:
        return 0

    def flush(self, timeout: float) -> int:
        return 0


@pytest.fixture
def fake_producer_client() -> FakeProducerClient:
    return FakeProducerClient()


@pytest.fixture
def event_producer(fake_producer_client: FakeProducerClient) -> EventProducer:
    return EventProducer("resume-profile-service", client=fake_producer_client)


@pytest.fixture
def resume_storage(tmp_path) -> LocalResumeStorage:
    return LocalResumeStorage(base_dir=tmp_path / "resumes")


# ---------------------------------------------------------------------------
# Fake repositories — same method surface as profiles/repository.py's
# ResumeRepository / CandidateProfileRepository, backed by an in-memory
# dict instead of a database.
# ---------------------------------------------------------------------------


class FakeResumeRepository:
    def __init__(self) -> None:
        self.by_id: dict[ResumeId, Resume] = {}

    async def get(self, resume_id: ResumeId) -> Resume | None:
        return self.by_id.get(resume_id)

    async def list_for_user(self, user_id: UserId) -> list[Resume]:
        return sorted(
            (r for r in self.by_id.values() if r.user_id == user_id),
            key=lambda r: r.uploaded_at,
            reverse=True,
        )

    async def add(self, resume: Resume) -> Resume:
        self.by_id[resume.id] = resume
        return resume

    async def set_status(self, resume_id: ResumeId, status: ResumeStatus) -> Resume | None:
        resume = self.by_id.get(resume_id)
        if resume is None:
            return None
        updated = resume.model_copy(update={"status": status})
        self.by_id[resume_id] = updated
        return updated

    async def mark_parsed(
        self, resume_id: ResumeId, raw_text: str, parsed_at: datetime
    ) -> Resume | None:
        resume = self.by_id.get(resume_id)
        if resume is None:
            return None
        updated = resume.model_copy(
            update={
                "raw_text": raw_text,
                "status": ResumeStatus.PARSED,
                "parsed_at": parsed_at,
                "parse_error": None,
            }
        )
        self.by_id[resume_id] = updated
        return updated

    async def mark_parse_failed(self, resume_id: ResumeId, parse_error: str) -> Resume | None:
        resume = self.by_id.get(resume_id)
        if resume is None:
            return None
        updated = resume.model_copy(
            update={"status": ResumeStatus.PARSE_FAILED, "parse_error": parse_error}
        )
        self.by_id[resume_id] = updated
        return updated

    async def archive(self, resume_id: ResumeId) -> Resume | None:
        return await self.set_status(resume_id, ResumeStatus.ARCHIVED)


class FakeCandidateProfileRepository:
    def __init__(self) -> None:
        self.by_id: dict[ProfileId, CandidateProfile] = {}

    async def get(self, profile_id: ProfileId) -> CandidateProfile | None:
        return self.by_id.get(profile_id)

    async def get_by_resume(self, resume_id: ResumeId) -> CandidateProfile | None:
        return next((p for p in self.by_id.values() if p.resume_id == resume_id), None)

    async def list_for_user(self, user_id: UserId) -> list[CandidateProfile]:
        return sorted(
            (p for p in self.by_id.values() if p.user_id == user_id),
            key=lambda p: p.generated_at,
            reverse=True,
        )

    async def add(self, profile: CandidateProfile) -> CandidateProfile:
        self.by_id[profile.id] = profile
        return profile

    async def archive_for_resume(self, resume_id: ResumeId) -> CandidateProfile | None:
        profile = await self.get_by_resume(resume_id)
        if profile is None:
            return None
        updated = profile.model_copy(update={"status": ProfileStatus.ARCHIVED})
        self.by_id[profile.id] = updated
        return updated


@pytest.fixture
def resume_repository() -> FakeResumeRepository:
    return FakeResumeRepository()


@pytest.fixture
def profile_repository() -> FakeCandidateProfileRepository:
    return FakeCandidateProfileRepository()


def make_resume(**overrides: Any) -> Resume:
    defaults: dict[str, Any] = {
        "id": ResumeId(uuid4()),
        "user_id": UserId(uuid4()),
        "file_name": "resume.txt",
        "storage_uri": "/tmp/does-not-matter.txt",
        "raw_text": None,
        "status": ResumeStatus.PARSING,
        "uploaded_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return Resume(**defaults)
