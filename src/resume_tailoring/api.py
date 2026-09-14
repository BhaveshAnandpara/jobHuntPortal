"""Resume Tailoring Service API router (docs/tailored-resume/TECH.md Task 6).

Owned endpoints:
    POST /jobs/{job_id}/tailored-resume
    GET  /jobs/{job_id}/tailored-resume
    GET  /jobs/{job_id}/tailored-resume/download

Mounted into the app in api/main.py. Routes hang off `/jobs/{job_id}/...`
rather than a `/tailored-resumes` collection of their own, following
`contacts/api/routes.py`'s precedent for a sub-resource that only ever
exists in the context of one job.

**Where the response models live.** Inline here rather than in
`shared/types/api/`, for the same reason `resume_tailoring/models.py`
records for `TailoredResume`: that package holds contracts *between*
components, and nothing outside this component consumes a tailored resume
except this router. CLAUDE.md's "check whether an existing shared contract
already exists before introducing one" points the same way — no existing
shared type covers this, and none needs to be minted to ship the feature.

**Persistence ordering.** The `.docx` is written to storage *before* the
`tailored_resumes` row is inserted, so a row never points at a file that
was never written; the reverse ordering is impossible anyway, since
`TailoredResumeRepository.add` mints the row id itself and the file path
must be known before the row is written. The document id in the filename
is therefore a fresh uuid minted here purely to keep one generation's file
from overwriting another's — exactly the uniqueness
`LocalTailoredResumeStorage` documents for that path component. A failed
insert leaves an unreferenced file, which is inert (nothing reads the
storage directory except by `storage_uri`).
"""

from __future__ import annotations

import io
import re
import unicodedata
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Protocol
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.auth import CurrentUserIdDependency
from infrastructure.llm import LlmNotConfiguredError, get_user_llm_client
from infrastructure.logging import format_context, get_logger
from resume_tailoring.document import build_resume_document
from resume_tailoring.errors import TailoringError
from resume_tailoring.models import TailoredResume
from resume_tailoring.repository import TailoredResumeRepository
from resume_tailoring.service import TailoringResult, tailor_resume
from resume_tailoring.storage import LocalTailoredResumeStorage
from shared.errors.codes import ErrorCode
from shared.types.ids import JobId, UserId

router = APIRouter(tags=["resume-tailoring"])
logger = get_logger(__name__)

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
"""Office Open XML word-processing document — what `.docx` bytes are."""

LLM_NOT_CONFIGURED_MESSAGE = "Add your AI provider key in Settings to use this feature."
"""Rendered verbatim by the frontend — keep it identical to the message
`POST /resumes` and `POST /jobs/ingest-url` already return, so the gated
state looks the same everywhere it can appear."""

NO_IMPROVEMENT_MESSAGE = "Your current resume is already well-suited for this job."
"""docs/tailored-resume/PRODUCT.md's exact wording for the legitimate
"nothing worth generating" outcome — a 200, never an error."""

# `NOT_FOUND` is the only code with a status of its own; everything else
# this component can raise follows `jobs/ingestion/api.py`'s precedent of
# surfacing as 400 (including `LLM_PROVIDER_ERROR`, which that router also
# maps to 400 rather than a 5xx — the caller's configured provider failed,
# and the caller is the one who can act on it).
_STATUS_BY_ERROR_CODE = {
    ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCode.VALIDATION_ERROR: status.HTTP_400_BAD_REQUEST,
    ErrorCode.LLM_PROVIDER_ERROR: status.HTTP_400_BAD_REQUEST,
    ErrorCode.LLM_NOT_CONFIGURED: status.HTTP_400_BAD_REQUEST,
}


# ---------------------------------------------------------------------------
# Response contracts
# ---------------------------------------------------------------------------


class TailoringResponse(BaseModel):
    """The outcome of one `POST /jobs/{job_id}/tailored-resume`.

    Both outcomes are successes, distinguished by `improved` and by the
    status code: 201 when a document was created (`id` set), 200 when the
    attempt honestly found nothing worth creating (`message` set, and no
    row or file persisted). `base_score`/`achieved_score` are always
    present so the frontend can show the comparison either way.
    """

    improved: bool
    base_score: float
    achieved_score: float
    id: UUID | None = None
    message: str | None = None
    # The skill-level breakdown behind the two scores, present on BOTH
    # outcomes — a user told "your resume is already well-suited" is
    # exactly the user who wants to see which required skills matched and
    # which are still missing. Straight passthrough of
    # `TailoringResult`'s four lists; no LLM call and no scoring rule
    # lives at this layer.
    base_matched_skills: list[str] = []
    base_missing_skills: list[str] = []
    tailored_matched_skills: list[str] = []
    tailored_missing_skills: list[str] = []


class TailoredResumeSummary(BaseModel):
    """Metadata for an already-generated tailored resume.

    Deliberately omits `storage_uri` (a server-side filesystem path) and
    `user_id`/`source_resume_id` — the caller downloads by job, and the
    route is already scoped to the caller's own identity.
    """

    id: UUID
    base_score: float
    achieved_score: float
    created_at: datetime


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


class TailorResumeFn(Protocol):
    """`resume_tailoring.service.tailor_resume`'s signature, as a seam.

    A dependency rather than a direct call so API tests can drive the three
    routes with a scripted `TailoringResult` — the service's own behavior
    is covered by `tests/resume_tailoring/test_service.py`, and re-proving
    it through HTTP would only test the fakes twice.
    """

    async def __call__(self, job_id: JobId, user_id: UserId) -> TailoringResult: ...


async def get_session() -> AsyncIterator[AsyncSession]:
    """One transactional session per request.

    Goes through this component's own `db.py` seam (rather than
    `infrastructure.database.get_session`) so a test binds the component's
    database in exactly one place — the same seam its service path uses.
    `tailoring_session_scope` commits on clean exit and rolls back on any
    exception, so nothing here manages the transaction by hand.
    """
    # Imported lazily so mounting this router in api/main.py never requires
    # a configured database — the same rule every other component's api
    # dependencies follow.
    from resume_tailoring.db import tailoring_session_scope

    async with tailoring_session_scope() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_repository(session: SessionDep) -> TailoredResumeRepository:
    return TailoredResumeRepository(session)


def get_storage() -> LocalTailoredResumeStorage:
    return LocalTailoredResumeStorage()


def get_tailor_resume() -> TailorResumeFn:
    return tailor_resume


RepositoryDep = Annotated[TailoredResumeRepository, Depends(get_repository)]
StorageDep = Annotated[LocalTailoredResumeStorage, Depends(get_storage)]
TailorResumeDep = Annotated[TailorResumeFn, Depends(get_tailor_resume)]


def _llm_not_configured() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": ErrorCode.LLM_NOT_CONFIGURED.value,
            "message": LLM_NOT_CONFIGURED_MESSAGE,
        },
    )


async def require_llm_configured(user_id: CurrentUserIdDependency) -> None:
    """Reject the request unless the caller has a validated LLM config.

    Declared as a route-level `dependencies=[...]` entry, which FastAPI
    solves before the handler's own parameters, so the gate fires before a
    session is opened, before either LLM call is made, and before any
    document is built — "blocked immediately, never discovered deep inside
    a pipeline" (docs/llm-provider-config/PRODUCT.md). Identical in shape
    and message to `POST /resumes` and `POST /jobs/ingest-url`; each router
    declares its own copy because a component may not import another
    component's modules.

    Only the generating route is gated. Reading metadata or downloading an
    already-generated document spends nobody's LLM quota, so a user whose
    key was later removed can still retrieve what they already have.
    """
    try:
        await get_user_llm_client(user_id)
    except LlmNotConfiguredError as error:
        logger.info(
            "Resume tailoring blocked: no validated LLM configuration | %s",
            format_context(user_id=user_id),
        )
        raise _llm_not_configured() from error


def _http_error(error: TailoringError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_BY_ERROR_CODE.get(error.code, status.HTTP_400_BAD_REQUEST),
        detail={"code": error.code.value, "message": error.message},
    )


def _skill_breakdown(result: TailoringResult) -> dict[str, list[str]]:
    """The four skill lists, copied verbatim onto either response body."""
    return {
        "base_matched_skills": list(result.base_matched_skills),
        "base_missing_skills": list(result.base_missing_skills),
        "tailored_matched_skills": list(result.tailored_matched_skills),
        "tailored_missing_skills": list(result.tailored_missing_skills),
    }


def _not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": ErrorCode.NOT_FOUND.value, "message": message},
    )


def _document_download_response(content: bytes, *, filename: str) -> StreamingResponse:
    """Stream `content` back as a file-download response.

    The first file download in this app. Kept deliberately generic — bytes
    plus a filename plus a media type — so an eventual "download my
    original resume" route reuses it as-is. `Content-Length` is set
    explicitly because a `StreamingResponse` does not infer one, and
    without it a browser cannot show download progress.
    """
    return StreamingResponse(
        io.BytesIO(content),
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(content)),
        },
    )


FILENAME_SUFFIX = "Resume"
"""Trailing word on a named download — `Aoife_Devlin_..._Resume.docx`."""

_FILENAME_SEPARATOR = "_"
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9]+")


def sanitize_filename_part(value: str | None) -> str:
    """Reduce one free-text field to a filename-safe `Word_Word` fragment.

    Unlike `job_id`, everything in a download filename now comes from user
    data — a candidate's name, an employer's name, a job title — any of
    which may contain quotes, slashes, path traversal, newlines, or
    non-Latin characters. So the rule here is an allowlist, not a blocklist:
    accented letters are folded to their ASCII base (`Zoë` -> `Zoe`) and
    **every** remaining non-alphanumeric run, spaces included, collapses to
    a single underscore, which is then trimmed from both ends. Nothing
    outside `[A-Za-z0-9_]` can survive, so no quoting is needed in the
    `Content-Disposition` header and no path separator can reach a client's
    filesystem. Original casing is kept — it is the only thing that keeps
    the result readable.

    A value that contains no ASCII alphanumerics at all (a purely CJK
    company name, say) yields `""`, which the caller drops like any other
    absent field rather than rendering as an empty segment.
    """
    if not value:
        return ""
    folded = unicodedata.normalize("NFKD", value)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    return _UNSAFE_FILENAME_CHARS.sub(_FILENAME_SEPARATOR, ascii_only).strip(
        _FILENAME_SEPARATOR
    )


def build_document_filename(
    *,
    job_id: UUID,
    full_name: str | None = None,
    company: str | None = None,
    role: str | None = None,
    version: int = 1,
) -> str:
    """`<Name>_<Company>_<Role>_Resume.docx`, with `_v2`, `_v3`, ... on
    regenerations.

    Segments the caller could not resolve are dropped rather than filled in
    with a placeholder, leaving no doubled underscore behind — the same
    "omit what you don't know, never invent it" rule the document renderer
    follows for contact details. When none of the three resolve, the name
    falls back to the original `tailored-resume-<job_id>.docx`, which is
    ugly but unique and never collides across jobs.

    `version` is the count of rows this job has (see
    `TailoredResumeRepository.count_for_job`): the first generation is
    version 1 and takes no suffix, so a user who never regenerates never
    sees version numbering at all.
    """
    segments = [
        part
        for part in (
            sanitize_filename_part(full_name),
            sanitize_filename_part(company),
            sanitize_filename_part(role),
        )
        if part
    ]
    if not segments:
        return f"tailored-resume-{job_id}.docx"

    segments.append(FILENAME_SUFFIX)
    if version > 1:
        segments.append(f"v{version}")
    return f"{_FILENAME_SEPARATOR.join(segments)}.docx"


class DocumentNaming(BaseModel):
    """The three human-readable fields a download filename is built from.

    Every one is optional: a job row that has since been deleted, or a
    profile with no extracted name, simply contributes nothing to the
    filename.
    """

    full_name: str | None = None
    company: str | None = None
    role: str | None = None


class DocumentNamingFn(Protocol):
    async def __call__(self, record: TailoredResume) -> DocumentNaming: ...


def get_document_naming(session: SessionDep) -> DocumentNamingFn:
    """Resolve a stored tailored resume's candidate name, company, and role.

    A dependency (rather than an inline lookup) for two reasons: it is the
    only cross-component read this router performs, so isolating it keeps
    that fact visible; and it lets the route's own tests name a file without
    standing up two other components' tables.

    Both reads are read-only and go through the owning component's own
    repository — the same narrow, documented grant
    `resume_tailoring/dependencies.py` already declares for
    `JobRepository` and `CandidateProfileRepository`, taken here for a
    strictly smaller purpose: three display strings, used for nothing but
    the `Content-Disposition` header. The imports are lazy for the same
    reason that module's are — importing this router must not require a
    configured database.
    """

    async def resolve(record: TailoredResume) -> DocumentNaming:
        from jobs.repository import JobRepository
        from profiles.repository import CandidateProfileRepository

        job = await JobRepository(session).get(record.job_id)
        profile = await CandidateProfileRepository(session).get_by_resume(
            record.source_resume_id
        )
        contact = profile.contact if profile is not None else None
        return DocumentNaming(
            full_name=contact.full_name if contact is not None else None,
            company=job.company if job is not None else None,
            role=job.title if job is not None else None,
        )

    return resolve


DocumentNamingDep = Annotated[DocumentNamingFn, Depends(get_document_naming)]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/jobs/{job_id}/tailored-resume",
    response_model=TailoringResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_llm_configured)],
)
async def generate_tailored_resume(
    job_id: UUID,
    response: Response,
    user_id: CurrentUserIdDependency,
    repository: RepositoryDep,
    storage: StorageDep,
    run_tailoring: TailorResumeDep,
) -> TailoringResponse:
    """Generate a tailored resume for this job, if one would be better.

    Returns 201 with the new record's id when the tailored content cleared
    the 5-point gate, and 200 with `improved: false` when it did not —
    which is a legitimate answer, not an error, so nothing is persisted and
    no error body is returned.
    """
    try:
        result = await run_tailoring(JobId(job_id), user_id)
    except LlmNotConfiguredError as error:
        # `require_llm_configured` has already answered this question, so
        # reaching here means the configuration was removed between the
        # gate and the service's own `get_user_llm_client` call. Same body
        # either way, so the race is invisible to the caller.
        raise _llm_not_configured() from error
    except TailoringError as error:
        logger.info(
            "Resume tailoring request failed | %s",
            format_context(
                job_id=job_id,
                user_id=user_id,
                error_code=error.code.value,
                error=error.message,
            ),
        )
        raise _http_error(error) from error

    if not result.improved or result.tailored_profile is None:
        response.status_code = status.HTTP_200_OK
        return TailoringResponse(
            improved=False,
            base_score=result.base_score,
            achieved_score=result.achieved_score,
            message=NO_IMPROVEMENT_MESSAGE,
            **_skill_breakdown(result),
        )

    content = build_resume_document(result.tailored_profile)
    storage_uri = storage.save(user_id, uuid4(), job_id, content)
    record = await repository.add(
        user_id=user_id,
        job_id=JobId(job_id),
        source_resume_id=result.tailored_profile.resume_id,
        storage_uri=storage_uri,
        base_score=result.base_score,
        achieved_score=result.achieved_score,
    )

    logger.info(
        "Tailored resume generated | %s",
        format_context(
            job_id=job_id,
            user_id=user_id,
            tailored_resume_id=record.id,
            base_score=result.base_score,
            achieved_score=result.achieved_score,
            bytes=len(content),
        ),
    )
    return TailoringResponse(
        improved=True,
        id=record.id,
        base_score=result.base_score,
        achieved_score=result.achieved_score,
        **_skill_breakdown(result),
    )


@router.get("/jobs/{job_id}/tailored-resume", response_model=TailoredResumeSummary)
async def get_tailored_resume(
    job_id: UUID,
    user_id: CurrentUserIdDependency,
    repository: RepositoryDep,
) -> TailoredResumeSummary:
    """Metadata for this job's tailored resume, or 404 if none exists.

    Not gated on LLM configuration — this reads existing state and spends
    no quota. A job belonging to another user is indistinguishable from a
    job with no tailored resume (`get_for_job` is scoped by `user_id`),
    which is the same non-disclosure rule `TailoringError` documents.
    """
    record = await repository.get_for_job(user_id, JobId(job_id))
    if record is None:
        raise _not_found(f"no tailored resume has been generated for job {job_id}")
    return TailoredResumeSummary(
        id=record.id,
        base_score=record.base_score,
        achieved_score=record.achieved_score,
        created_at=record.created_at,
    )


@router.get("/jobs/{job_id}/tailored-resume/download")
async def download_tailored_resume(
    job_id: UUID,
    user_id: CurrentUserIdDependency,
    repository: RepositoryDep,
    storage: StorageDep,
    resolve_naming: DocumentNamingDep,
) -> StreamingResponse:
    """Download this job's tailored resume as a `.docx` attachment.

    The file arrives named for the application it belongs to —
    `Aoife_Devlin_Riverside_General_Hospital_ICU_Staff_Nurse_Resume.docx` —
    because a download folder full of `tailored-resume-<uuid>.docx` is
    unusable the moment a user has applied to more than one job. The name is
    resolved at download time rather than stored, so renaming a job or
    correcting a name on the profile is reflected the next time the document
    is fetched, with no migration and no stale copy.
    """
    record = await repository.get_for_job(user_id, JobId(job_id))
    # The ownership re-check is redundant with `get_for_job`'s `user_id`
    # filter and deliberately kept: it is the last line before bytes leave
    # the process, and it costs one comparison.
    if record is None or record.user_id != user_id:
        raise _not_found(f"no tailored resume has been generated for job {job_id}")

    try:
        content = storage.read(record.storage_uri)
    except FileNotFoundError as error:
        # The row and the disk have diverged — surfaced rather than papered
        # over with empty bytes (see LocalTailoredResumeStorage.read), and
        # logged at error level because it means something deleted a file
        # this app still has a row for.
        logger.error(
            "Tailored resume file is missing for an existing row | %s",
            format_context(
                job_id=job_id, user_id=user_id, tailored_resume_id=record.id
            ),
        )
        raise _not_found(
            f"the tailored resume document for job {job_id} is no longer available"
        ) from error

    naming = await resolve_naming(record)
    # Append-only table, so the row count *is* this document's version:
    # the first generation is v1 and unsuffixed, a regeneration is `_v2`.
    version = await repository.count_for_job(user_id, JobId(job_id))
    filename = build_document_filename(
        job_id=job_id,
        full_name=naming.full_name,
        company=naming.company,
        role=naming.role,
        version=version,
    )
    return _document_download_response(content, filename=filename)


__all__ = [
    "DOCX_MEDIA_TYPE",
    "FILENAME_SUFFIX",
    "LLM_NOT_CONFIGURED_MESSAGE",
    "NO_IMPROVEMENT_MESSAGE",
    "DocumentNaming",
    "TailoredResumeSummary",
    "TailoringResponse",
    "build_document_filename",
    "get_document_naming",
    "require_llm_configured",
    "router",
    "sanitize_filename_part",
]
