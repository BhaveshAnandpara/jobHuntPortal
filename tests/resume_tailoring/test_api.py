"""Route-level tests for Resume Tailoring Service's three endpoints
(docs/tailored-resume/TECH.md Task 6).

Requests go through `httpx.ASGITransport` so the app shares the async
fixture's event loop (SQLAlchemy's async engine is loop-bound, and a
`TestClient` portal would run the app on a different one) — the same
harness shape as `tests/jobs/ingestion/test_llm_gating.py`.

What is real here and what is faked is deliberate:

* real `TailoredResumeRepository` against a real (SQLite in-memory)
  `tailored_resumes` table, so "nothing persisted" and "a row exists" are
  claims about actual SQL, not about a fake's bookkeeping
* real `LocalTailoredResumeStorage` rooted at `tmp_path`, and the real
  `build_resume_document`, so the download route returns bytes a `.docx`
  reader can actually open
* only `tailor_resume` itself is scripted, at this router's
  `get_tailor_resume` seam — the service's own generation/scoring/gate
  behavior is covered by `tests/resume_tailoring/test_service.py`, and
  re-proving it through HTTP would only exercise the fakes twice
* the LLM-config gate is overridden to a no-op here; it has its own file
  (`test_llm_gating.py`), which runs it for real against a real
  `user_llm_config` table

No PostgreSQL, Kafka broker, or LLM provider is required.
"""

from __future__ import annotations

import io
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from infrastructure.auth.dependencies import get_current_user_id
from profiles.repository import to_resume_profile
from resume_tailoring import db as tailoring_db
from resume_tailoring.api import (
    DOCX_MEDIA_TYPE,
    NO_IMPROVEMENT_MESSAGE,
    get_document_naming,
    get_storage,
    get_tailor_resume,
    require_llm_configured,
    router,
)
from resume_tailoring.errors import TailoringError
from resume_tailoring.models import TailoredResumeRecord
from resume_tailoring.repository import TailoredResumeRepository
from resume_tailoring.service import TailoringResult
from resume_tailoring.storage import LocalTailoredResumeStorage
from shared.errors.codes import ErrorCode
from shared.types.dto import ResumeProfile
from shared.types.ids import JobId, UserId
from tests.resume_tailoring.conftest import make_candidate_profile, naming_override

pytestmark = pytest.mark.asyncio

BASE_SCORE = 0.62
ACHIEVED_SCORE = 0.81
CANDIDATE_NAME = "Aoife Devlin"
JOB_COMPANY = "Riverside General Hospital"
JOB_ROLE = "ICU Staff Nurse"
EXPECTED_FILENAME_STEM = "Aoife_Devlin_Riverside_General_Hospital_ICU_Staff_Nurse_Resume"
BASE_MATCHED = ["Ventilator management"]
BASE_MISSING = ["ALS certification", "Continuous renal replacement therapy"]
TAILORED_MATCHED = ["Ventilator management", "ALS certification"]
TAILORED_MISSING = ["Continuous renal replacement therapy"]
DOCX_MAGIC = b"PK\x03\x04"
"""A `.docx` is a zip archive, so every valid one starts with this."""


class ScriptedTailoring:
    """Stands in for `resume_tailoring.service.tailor_resume`.

    Returns `result`, or raises `error` when one is set, and records every
    call so a test can assert the route never reached the service (the
    gating file's whole point) as easily as that it did.
    """

    def __init__(self) -> None:
        self.result: TailoringResult | None = None
        self.error: Exception | None = None
        self.calls: list[tuple[JobId, UserId]] = []

    async def __call__(self, job_id: JobId, user_id: UserId) -> TailoringResult:
        self.calls.append((job_id, user_id))
        if self.error is not None:
            raise self.error
        assert self.result is not None, "test did not script a TailoringResult"
        return self.result


@dataclass
class Harness:
    app: FastAPI
    client: httpx.AsyncClient
    session_factory: async_sessionmaker[AsyncSession]
    owner: UserId
    job_id: JobId
    profile: ResumeProfile
    storage_dir: Path
    tailoring: ScriptedTailoring
    acting_user: UserId = field(init=False)

    def __post_init__(self) -> None:
        self.acting_user = self.owner

    def improves(self) -> None:
        self.tailoring.result = TailoringResult.improvement(
            base_score=BASE_SCORE,
            achieved_score=ACHIEVED_SCORE,
            tailored_profile=self.profile,
            base_matched_skills=list(BASE_MATCHED),
            base_missing_skills=list(BASE_MISSING),
            tailored_matched_skills=list(TAILORED_MATCHED),
            tailored_missing_skills=list(TAILORED_MISSING),
        )

    def does_not_improve(self, achieved: float = 0.64) -> None:
        self.tailoring.result = TailoringResult.no_improvement(
            base_score=BASE_SCORE,
            achieved_score=achieved,
            base_matched_skills=list(BASE_MATCHED),
            base_missing_skills=list(BASE_MISSING),
            tailored_matched_skills=list(TAILORED_MATCHED),
            tailored_missing_skills=list(TAILORED_MISSING),
        )

    async def stored_row(self, user_id: UserId | None = None):
        async with self.session_factory() as session:
            return await TailoredResumeRepository(session).get_for_job(
                user_id if user_id is not None else self.owner, self.job_id
            )

    def stored_files(self) -> list[Path]:
        return sorted(p for p in self.storage_dir.rglob("*") if p.is_file())

    async def generate(self) -> httpx.Response:
        return await self.client.post(f"/jobs/{self.job_id}/tailored-resume")

    async def get_metadata(self) -> httpx.Response:
        return await self.client.get(f"/jobs/{self.job_id}/tailored-resume")

    async def download(self) -> httpx.Response:
        return await self.client.get(f"/jobs/{self.job_id}/tailored-resume/download")


@pytest_asyncio.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    """An isolated in-memory SQLite DB holding only `tailored_resumes`.

    Scoped to this component's own table rather than a blanket
    `Base.metadata.create_all()`, for the reason
    `tests/resume_tailoring/test_repository.py` documents: `Base.metadata`
    is one process-wide registry, so a blanket create would depend on which
    other components' `models.py` an earlier test module imported.
    """
    pytest.importorskip(
        "aiosqlite", reason="async sqlite driver not installed; API tests need it"
    )
    from infrastructure.database import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all, tables=[TailoredResumeRecord.__table__]
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    tailoring_db.set_session_factory(session_factory)

    owner = UserId(uuid4())
    storage_dir = tmp_path / "tailored_resumes"
    storage = LocalTailoredResumeStorage(storage_dir)
    tailoring = ScriptedTailoring()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_tailor_resume] = lambda: tailoring
    # The gate has its own file; here it must never be the reason a route
    # fails, so it is replaced with a no-op that asks nothing of the DB.
    app.dependency_overrides[require_llm_configured] = lambda: None
    # The real naming dependency reads `jobs` and `candidate_profiles`,
    # which this DB deliberately does not carry; its own behavior is
    # covered by tests/resume_tailoring/test_naming.py.
    app.dependency_overrides[get_document_naming] = naming_override(
        full_name=CANDIDATE_NAME, company=JOB_COMPANY, role=JOB_ROLE
    )

    # The override reads `harness.acting_user` on every request (rather
    # than closing over one value), so a test can switch identity mid-test
    # to prove the cross-user isolation the routes promise.
    holder: dict[str, Harness] = {}
    app.dependency_overrides[get_current_user_id] = lambda: holder["harness"].acting_user

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        holder["harness"] = Harness(
            app=app,
            client=client,
            session_factory=session_factory,
            owner=owner,
            job_id=JobId(uuid4()),
            profile=to_resume_profile(make_candidate_profile(owner)),
            storage_dir=storage_dir,
            tailoring=tailoring,
        )
        yield holder["harness"]

    tailoring_db.set_session_factory(None)
    await engine.dispose()


# ---------------------------------------------------------------------------
# POST /jobs/{job_id}/tailored-resume
# ---------------------------------------------------------------------------


async def test_generation_above_the_gate_returns_201_and_persists_a_document(
    harness: Harness,
) -> None:
    harness.improves()

    response = await harness.generate()

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["improved"] is True
    assert body["base_score"] == pytest.approx(BASE_SCORE)
    assert body["achieved_score"] == pytest.approx(ACHIEVED_SCORE)
    assert body["message"] is None
    record_id = UUID(body["id"])

    # The service was asked about this job, on behalf of this caller.
    assert harness.tailoring.calls == [(harness.job_id, harness.owner)]

    # A real row exists, pointing at a real file holding real .docx bytes.
    row = await harness.stored_row()
    assert row is not None
    assert row.id == record_id
    assert row.job_id == harness.job_id
    assert row.user_id == harness.owner
    assert row.source_resume_id == harness.profile.resume_id
    assert row.base_score == pytest.approx(BASE_SCORE)
    assert row.achieved_score == pytest.approx(ACHIEVED_SCORE)

    files = harness.stored_files()
    assert len(files) == 1
    assert Path(row.storage_uri) == files[0]
    assert files[0].read_bytes().startswith(DOCX_MAGIC)


async def test_generation_below_the_gate_returns_200_and_persists_nothing(
    harness: Harness,
) -> None:
    """"No improvement" is a legitimate outcome, not an error — and it must
    leave no row and no file behind (PRODUCT.md: "nothing gets created at
    all")."""
    harness.does_not_improve(achieved=0.64)

    response = await harness.generate()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["improved"] is False
    assert body["id"] is None
    assert body["message"] == NO_IMPROVEMENT_MESSAGE
    assert body["message"] == "Your current resume is already well-suited for this job."
    assert body["base_score"] == pytest.approx(BASE_SCORE)
    assert body["achieved_score"] == pytest.approx(0.64)

    assert await harness.stored_row() is None
    assert harness.stored_files() == []

    # And nothing is retrievable afterwards, by either read route.
    assert (await harness.get_metadata()).status_code == 404
    assert (await harness.download()).status_code == 404


async def test_improved_response_carries_the_skill_breakdown(
    harness: Harness,
) -> None:
    """The four lists answer "why did the score change?" without a second
    LLM call — they are already computed, and now they reach the caller."""
    harness.improves()

    body = (await harness.generate()).json()

    assert body["base_matched_skills"] == BASE_MATCHED
    assert body["base_missing_skills"] == BASE_MISSING
    assert body["tailored_matched_skills"] == TAILORED_MATCHED
    assert body["tailored_missing_skills"] == TAILORED_MISSING


async def test_no_improvement_response_also_carries_the_skill_breakdown(
    harness: Harness,
) -> None:
    """"Already well-suited" is exactly when a user wants to see which
    skills are still missing, so the 200 body carries them too."""
    harness.does_not_improve()

    response = await harness.generate()
    body = response.json()

    assert response.status_code == 200
    assert body["improved"] is False
    assert body["base_matched_skills"] == BASE_MATCHED
    assert body["base_missing_skills"] == BASE_MISSING
    assert body["tailored_matched_skills"] == TAILORED_MATCHED
    assert body["tailored_missing_skills"] == TAILORED_MISSING


async def test_skill_breakdown_defaults_to_empty_lists_not_null(
    harness: Harness,
) -> None:
    """A result carrying no breakdown (an older caller, or a scorer that
    reported none) must still serialize four lists — the frontend renders
    them directly and must never receive `null`."""
    harness.tailoring.result = TailoringResult.improvement(
        base_score=BASE_SCORE,
        achieved_score=ACHIEVED_SCORE,
        tailored_profile=harness.profile,
    )

    body = (await harness.generate()).json()

    for field in (
        "base_matched_skills",
        "base_missing_skills",
        "tailored_matched_skills",
        "tailored_missing_skills",
    ):
        assert body[field] == [], field


async def test_generation_for_an_unknown_or_foreign_job_returns_404(
    harness: Harness,
) -> None:
    """`TailoringError(NOT_FOUND)` covers both "no such job/match" and "it
    belongs to someone else" — the route must not distinguish them either."""
    harness.tailoring.error = TailoringError(
        ErrorCode.NOT_FOUND, f"no job match found for job {harness.job_id}"
    )

    response = await harness.generate()

    assert response.status_code == 404, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "NOT_FOUND"
    assert str(harness.job_id) in detail["message"]
    assert await harness.stored_row() is None
    assert harness.stored_files() == []


async def test_generation_surfaces_an_llm_provider_failure_as_400(
    harness: Harness,
) -> None:
    harness.tailoring.error = TailoringError(
        ErrorCode.LLM_PROVIDER_ERROR, "could not score the tailored resume: timeout"
    )

    response = await harness.generate()

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "LLM_PROVIDER_ERROR"
    assert await harness.stored_row() is None


async def test_generation_requires_authentication(harness: Harness) -> None:
    harness.app.dependency_overrides.pop(get_current_user_id)

    response = await harness.generate()

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"
    assert harness.tailoring.calls == []


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/tailored-resume
# ---------------------------------------------------------------------------


async def test_get_returns_the_metadata_of_a_generated_resume(
    harness: Harness,
) -> None:
    harness.improves()
    created = (await harness.generate()).json()

    response = await harness.get_metadata()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == created["id"]
    assert body["base_score"] == pytest.approx(BASE_SCORE)
    assert body["achieved_score"] == pytest.approx(ACHIEVED_SCORE)
    assert body["created_at"]
    # Server-side details never leak onto the read contract.
    assert "storage_uri" not in body
    assert "user_id" not in body


async def test_get_without_a_generated_resume_returns_404(harness: Harness) -> None:
    """"Not tailored yet" is reported as 404 — the same "unset is not an
    error, but it is not a body either" convention used elsewhere."""
    response = await harness.get_metadata()

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}/tailored-resume/download
# ---------------------------------------------------------------------------


async def test_download_streams_the_generated_docx(harness: Harness) -> None:
    harness.improves()
    await harness.generate()

    response = await harness.download()

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == DOCX_MEDIA_TYPE
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{EXPECTED_FILENAME_STEM}.docx"'
    )
    assert response.headers["content-length"] == str(len(response.content))
    assert response.content.startswith(DOCX_MAGIC)

    # Round-trip: the bytes really are an openable document with the
    # tailored profile's content in it, not just a well-formed zip.
    docx = pytest.importorskip("docx", reason="python-docx is required to read back")
    document = docx.Document(io.BytesIO(response.content))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert harness.profile.contact is not None
    assert harness.profile.contact.full_name in text


def _disposition_filename(response: httpx.Response) -> str:
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="')
    return disposition.removeprefix('attachment; filename="').removesuffix('"')


async def test_download_filename_is_named_for_the_application(
    harness: Harness,
) -> None:
    """A download folder full of `tailored-resume-<uuid>.docx` is unusable
    after the second application, so the file is named for the candidate,
    the employer, and the role."""
    harness.improves()
    await harness.generate()

    filename = _disposition_filename(await harness.download())

    assert filename == f"{EXPECTED_FILENAME_STEM}.docx"
    # Sanitized: spaces became underscores and nothing outside the safe set
    # survives, so the header needs no quoting games.
    assert re.fullmatch(r"[A-Za-z0-9_]+\.docx", filename)
    assert str(harness.job_id) not in filename


async def test_first_generation_has_no_version_suffix(harness: Harness) -> None:
    harness.improves()
    await harness.generate()

    filename = _disposition_filename(await harness.download())

    assert filename == f"{EXPECTED_FILENAME_STEM}.docx"
    assert "_v" not in filename


async def test_regenerating_versions_the_filename(harness: Harness) -> None:
    """`tailored_resumes` is append-only, so the row count is the version:
    a second generation downloads as `_v2`, a third as `_v3`."""
    harness.improves()

    await harness.generate()
    assert _disposition_filename(await harness.download()) == (
        f"{EXPECTED_FILENAME_STEM}.docx"
    )

    await harness.generate()
    assert _disposition_filename(await harness.download()) == (
        f"{EXPECTED_FILENAME_STEM}_v2.docx"
    )

    await harness.generate()
    assert _disposition_filename(await harness.download()) == (
        f"{EXPECTED_FILENAME_STEM}_v3.docx"
    )


async def test_a_run_that_produced_nothing_does_not_bump_the_version(
    harness: Harness,
) -> None:
    """Only a *generated* document is a version. An attempt that did not
    clear the gate persists no row, so it must not shift the numbering."""
    harness.improves()
    await harness.generate()
    harness.does_not_improve()
    await harness.generate()

    assert _disposition_filename(await harness.download()) == (
        f"{EXPECTED_FILENAME_STEM}.docx"
    )


async def test_download_falls_back_when_nothing_can_be_named(
    harness: Harness,
) -> None:
    """A deleted job (or a profile with no extracted name) leaves the
    filename with nothing to say — it degrades to the unique, ugly form
    rather than emitting placeholders or a file called `_Resume.docx`."""
    harness.app.dependency_overrides[get_document_naming] = naming_override()
    harness.improves()
    await harness.generate()

    filename = _disposition_filename(await harness.download())

    assert filename == f"tailored-resume-{harness.job_id}.docx"


async def test_download_without_a_generated_resume_returns_404(
    harness: Harness,
) -> None:
    response = await harness.download()

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "NOT_FOUND"


async def test_download_returns_404_when_the_stored_file_has_vanished(
    harness: Harness,
) -> None:
    """A row pointing at a file that is gone is a real divergence — it is
    surfaced, never papered over with empty bytes."""
    harness.improves()
    await harness.generate()
    row = await harness.stored_row()
    assert row is not None
    Path(row.storage_uri).unlink()

    response = await harness.download()

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Cross-user isolation
# ---------------------------------------------------------------------------


async def test_another_user_cannot_read_or_download_this_users_tailored_resume(
    harness: Harness,
) -> None:
    """User B knowing user A's `job_id` must learn nothing — 404, not 403,
    so the response never confirms the resource exists."""
    harness.improves()
    assert (await harness.generate()).status_code == 201

    harness.acting_user = UserId(uuid4())

    metadata = await harness.get_metadata()
    download = await harness.download()

    assert metadata.status_code == 404, metadata.text
    assert metadata.json()["detail"]["code"] == "NOT_FOUND"
    assert download.status_code == 404, download.text
    assert download.json()["detail"]["code"] == "NOT_FOUND"
    # The owner is unaffected.
    harness.acting_user = harness.owner
    assert (await harness.get_metadata()).status_code == 200
    assert (await harness.download()).status_code == 200
