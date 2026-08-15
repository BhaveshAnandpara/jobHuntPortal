"""Business logic boundary for Resume/Profile Service. Called only by
profiles/api/; never imported by another component (see
docs/architecture/dependency-graph.md#1-compileimport-dependencies).

Orchestrates ResumeRepository / CandidateProfileRepository, the parsing
workflow (profiles/parsing.py), file storage (profiles/storage.py), and the
`profiles.updated` producer (profiles/events.py) on behalf of profiles/api/.
Mirrors the layering users/service.py uses: a typed `*Error` carrying a
shared `ErrorCode`, which the API layer maps to an HTTP status.
"""

from __future__ import annotations

import base64
import binascii
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from infrastructure.kafka.producer import EventProducer
from infrastructure.llm import LLMClient
from infrastructure.logging import format_context, get_logger
from profiles.events import publish_profile_updated
from profiles.parsing import SUPPORTED_EXTENSIONS, ResumeParsingError, parse_resume
from profiles.repository import (
    CandidateProfileRepository,
    ResumeRepository,
    to_resume_profile,
)
from profiles.storage import LocalResumeStorage
from shared.errors.codes import ErrorCode
from shared.events.payloads import ProfileUpdateSummary
from shared.types.api.profiles import CreateResumeRequest
from shared.types.domain.candidate_profile import CandidateProfile
from shared.types.domain.resume import Resume
from shared.types.dto import ResumeProfile
from shared.types.enums import ProfileChangeType, ResumeStatus
from shared.types.ids import ProfileId, ResumeId, UserId

_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB, per api-contracts.md#post-resumes

logger = get_logger(__name__)


class ProfileError(Exception):
    """Typed Resume/Profile Service failure carrying a shared `ErrorCode`,
    which the API layer maps to an HTTP status (see
    docs/architecture/api-contracts.md#resumeprofile-service).
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ProfileService:
    """Orchestrates ResumeRepository / CandidateProfileRepository, the
    parsing workflow, storage, and the `profiles.updated` producer on
    behalf of profiles/api/.

    `llm_client` is optional: routes that don't touch parsing (list,
    delete, read) must keep working even if the LLM Provider Layer's
    `LLMClient` can't be constructed for some reason (see
    profiles/api/dependencies.py). When it is `None`,
    `run_parsing_workflow` fails every resume with
    `ErrorCode.LLM_PROVIDER_ERROR` rather than raising out of route
    resolution.
    """

    def __init__(
        self,
        resumes: ResumeRepository,
        profiles: CandidateProfileRepository,
        *,
        producer: EventProducer,
        storage: LocalResumeStorage,
        llm_client: LLMClient | None = None,
    ) -> None:
        self._resumes = resumes
        self._profiles = profiles
        self._producer = producer
        self._storage = storage
        self._llm_client = llm_client

    async def upload_resume(self, request: CreateResumeRequest, user_id: UserId) -> Resume:
        """`POST /resumes` — validate, store the file, and insert the
        `Resume` row at `status=PARSING` (per
        docs/architecture/component-contracts.md#post-resumes-upload:
        "Resume.status UPLOADED -> PARSING (immediately after insert)").

        Parsing itself is not run here — the caller (profiles/api/routes.py)
        schedules `run_parsing_workflow` as a background step once this
        method returns, so the upload request completes without waiting on
        the LLM call.
        """
        logger.info(
            "Resume upload received | %s",
            format_context(user_id=user_id, file_name=request.file_name),
        )

        suffix = Path(request.file_name).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise ProfileError(
                ErrorCode.VALIDATION_ERROR,
                f"unsupported file type: {suffix or '(none)'}",
            )

        # `CreateResumeRequest.file_content` is the base64 string the
        # frontend sends inside the JSON body (see shared/types/api/profiles.py
        # and docs/frontend/api-mapping.md). Decode it explicitly here rather
        # than relying on Pydantic's `Base64Bytes` so malformed base64
        # produces this service's normalized `{"code","message"}` error
        # shape (via ProfileError/_http_error) instead of a raw Pydantic
        # ValidationError surfacing as FastAPI's default 422 array body —
        # there is no global RequestValidationError handler in src/api/main.py
        # to normalize that shape.
        try:
            file_content = base64.b64decode(request.file_content, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ProfileError(
                ErrorCode.VALIDATION_ERROR, "file_content is not valid base64"
            ) from exc

        if not file_content:
            raise ProfileError(ErrorCode.VALIDATION_ERROR, "file_content must not be empty")
        if len(file_content) > _MAX_FILE_SIZE_BYTES:
            raise ProfileError(ErrorCode.VALIDATION_ERROR, "file exceeds the 10MB upload limit")

        # NOTE (contract gap — see final report): component-contracts.md
        # also requires validating that `user_id` exists (NOT_FOUND
        # otherwise). There is no `GET /users/{id}` read API to check
        # existence, and docs/architecture/ownership.md#rule-prefer-a-contract-over-reaching-into-internal-state
        # forbids a direct cross-component table read. Format validation is
        # already enforced by `UserId`/`CreateResumeRequest` (a UUID); actual
        # existence is intentionally not checked here — flagged rather than
        # silently resolved.

        resume_id = ResumeId(uuid4())
        storage_uri = self._storage.save(
            user_id, resume_id, request.file_name, file_content
        )
        now = datetime.now(UTC)
        await self._resumes.add(
            Resume(
                id=resume_id,
                user_id=user_id,
                file_name=request.file_name,
                storage_uri=storage_uri,
                raw_text=None,
                status=ResumeStatus.UPLOADED,
                uploaded_at=now,
            )
        )
        resume = await self._resumes.set_status(resume_id, ResumeStatus.PARSING)
        assert resume is not None  # just inserted above
        logger.info(
            "Resume accepted for parsing | %s",
            format_context(resume_id=resume_id, user_id=user_id, status=resume.status.value),
        )
        return resume

    async def run_parsing_workflow(self, resume_id: ResumeId) -> CandidateProfile | None:
        """The Resume Parsing Workflow
        (docs/architecture/component-contracts.md#resume-parsing-workflow-internal-background-step-not-langgraph--see-langgraph-statemd):
        extract text -> LLM structured extraction -> persist
        `CandidateProfile` -> publish `ProfileUpdatedEvent` (success only).

        Returns `None` if the resume no longer exists, or if parsing
        failed (in which case `Resume.status` is set to `PARSE_FAILED` and
        no event is published — errors are handled here, not raised, since
        this runs as a background step with no caller waiting on an
        exception).
        """
        resume = await self._resumes.get(resume_id)
        if resume is None:
            return None

        started = time.monotonic()
        logger.info("Resume parsing started | %s", format_context(resume_id=resume_id))

        try:
            if self._llm_client is None:
                raise ResumeParsingError(
                    ErrorCode.LLM_PROVIDER_ERROR, "no LLM client is configured"
                )
            file_content = self._storage.read(resume.storage_uri)
            raw_text, profile = await parse_resume(
                resume,
                client=self._llm_client,
                file_content=file_content,
            )
        except ResumeParsingError as error:
            duration_ms = round((time.monotonic() - started) * 1000, 1)
            logger.exception(
                "Resume parsing failed | %s",
                format_context(
                    resume_id=resume_id,
                    error_code=error.code.value,
                    error=error.message,
                    duration_ms=duration_ms,
                ),
            )
            await self._resumes.mark_parse_failed(resume_id, error.message)
            return None

        now = datetime.now(UTC)
        await self._resumes.mark_parsed(resume_id, raw_text, now)
        await self._profiles.add(profile)
        publish_profile_updated(
            ProfileUpdateSummary(
                profile_id=profile.id,
                user_id=profile.user_id,
                resume_id=resume.id,
                change_type=ProfileChangeType.CREATED,
                updated_at=now,
            ),
            producer=self._producer,
        )
        duration_ms = round((time.monotonic() - started) * 1000, 1)
        logger.info(
            "Resume parsing completed | %s",
            format_context(resume_id=resume_id, profile_id=profile.id, duration_ms=duration_ms),
        )
        return profile

    async def list_resumes(self, user_id: UserId) -> list[Resume]:
        """`GET /resumes`."""
        return await self._resumes.list_for_user(user_id)

    async def delete_resume(self, resume_id: ResumeId) -> None:
        """`DELETE /resumes/{resume_id}` — soft delete: `Resume.status` and
        the derived `CandidateProfile.status` (if one exists) both move to
        `ARCHIVED`; `ProfileUpdatedEvent(change_type=ARCHIVED)` is published
        only when a profile actually existed to archive (a resume that
        never finished parsing has none — nothing to report downstream).

        NOTE: component-contracts.md also lists "resume belongs to the
        requesting user" as a validation step. No auth/identity context is
        threaded through this API today (no component has one yet), so
        ownership is not enforced here — consistent with every other
        component in this Wave 1 pass.
        """
        resume = await self._resumes.get(resume_id)
        if resume is None:
            raise ProfileError(ErrorCode.NOT_FOUND, "resume does not exist")

        await self._resumes.archive(resume_id)
        archived_profile = await self._profiles.archive_for_resume(resume_id)

        if archived_profile is not None:
            publish_profile_updated(
                ProfileUpdateSummary(
                    profile_id=archived_profile.id,
                    user_id=archived_profile.user_id,
                    resume_id=resume_id,
                    change_type=ProfileChangeType.ARCHIVED,
                    updated_at=datetime.now(UTC),
                ),
                producer=self._producer,
            )
        logger.info(
            "Resume deleted | %s",
            format_context(resume_id=resume_id, had_profile=archived_profile is not None),
        )

    async def list_profiles(self, user_id: UserId) -> list[ResumeProfile]:
        """`GET /profiles`."""
        profiles = await self._profiles.list_for_user(user_id)
        return [to_resume_profile(profile) for profile in profiles]

    async def get_profile(self, profile_id: ProfileId) -> ResumeProfile:
        """`GET /profiles/{profile_id}`."""
        profile = await self._profiles.get(profile_id)
        if profile is None:
            raise ProfileError(ErrorCode.NOT_FOUND, "profile does not exist")
        return to_resume_profile(profile)


__all__ = ["ProfileError", "ProfileService"]
