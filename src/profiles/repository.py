"""Persistence boundary for Resume/Profile Service's owned tables
(`resumes`, `candidate_profiles`).

No other component may import this module — see
docs/architecture/ownership.md#rule-prefer-a-contract-over-reaching-into-internal-state.
Other components read this data through `GET /resumes` / `GET /profiles`
(docs/architecture/api-contracts.md#resumeprofile-service) or the
`profiles.updated` event, never through these classes.

Repositories take and return canonical domain types
(`shared.types.domain.resume.Resume`,
`shared.types.domain.candidate_profile.CandidateProfile`); the `*Record`
SQLAlchemy models in models.py never leave this module.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from profiles.models import CandidateProfileRecord, ResumeRecord
from shared.types.domain.candidate_profile import CandidateProfile
from shared.types.domain.resume import Resume
from shared.types.dto import EducationEntry, ResumeProfile
from shared.types.enums import ProfileStatus, ResumeStatus
from shared.types.ids import ProfileId, ResumeId, UserId


def _to_resume(record: ResumeRecord) -> Resume:
    return Resume(
        id=ResumeId(record.id),
        user_id=UserId(record.user_id),
        file_name=record.file_name,
        storage_uri=record.storage_uri,
        raw_text=record.raw_text,
        status=record.status,
        uploaded_at=record.uploaded_at,
        parsed_at=record.parsed_at,
        parse_error=record.parse_error,
    )


def _to_candidate_profile(record: CandidateProfileRecord) -> CandidateProfile:
    return CandidateProfile(
        id=ProfileId(record.id),
        user_id=UserId(record.user_id),
        resume_id=ResumeId(record.resume_id),
        title=record.title,
        summary=record.summary,
        skills=list(record.skills),
        experience_years=record.experience_years,
        seniority=record.seniority,
        education=[EducationEntry(**entry) for entry in record.education],
        certifications=list(record.certifications),
        projects=list(record.projects),
        industries=list(record.industries),
        target_roles=list(record.target_roles),
        status=record.status,
        generated_at=record.generated_at,
    )


def to_resume_profile(profile: CandidateProfile) -> ResumeProfile:
    """Assemble the canonical `ResumeProfile` read type
    (docs/architecture/shared-types.md#resumeprofile). Every `ResumeProfile`
    field is carried on `CandidateProfile`, so this is a pure projection —
    it is never persisted, it is rebuilt on every read.
    """
    return ResumeProfile(
        profile_id=profile.id,
        resume_id=profile.resume_id,
        user_id=profile.user_id,
        title=profile.title,
        summary=profile.summary,
        skills=profile.skills,
        experience_years=profile.experience_years,
        seniority=profile.seniority,
        education=profile.education,
        certifications=profile.certifications,
        projects=profile.projects,
        industries=profile.industries,
        target_roles=profile.target_roles,
        status=profile.status,
    )


class ResumeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, resume_id: ResumeId) -> Resume | None:
        record = await self._session.get(ResumeRecord, resume_id)
        return _to_resume(record) if record is not None else None

    async def list_for_user(self, user_id: UserId) -> list[Resume]:
        result = await self._session.scalars(
            select(ResumeRecord)
            .where(ResumeRecord.user_id == user_id)
            .order_by(ResumeRecord.uploaded_at.desc())
        )
        return [_to_resume(record) for record in result]

    async def add(self, resume: Resume) -> Resume:
        self._session.add(
            ResumeRecord(
                id=resume.id,
                user_id=resume.user_id,
                file_name=resume.file_name,
                storage_uri=resume.storage_uri,
                raw_text=resume.raw_text,
                status=resume.status,
                uploaded_at=resume.uploaded_at,
                parsed_at=resume.parsed_at,
                parse_error=resume.parse_error,
            )
        )
        await self._session.flush()
        return resume

    async def set_status(self, resume_id: ResumeId, status: ResumeStatus) -> Resume | None:
        record = await self._session.get(ResumeRecord, resume_id)
        if record is None:
            return None
        record.status = status
        await self._session.flush()
        return _to_resume(record)

    async def mark_parsed(
        self, resume_id: ResumeId, raw_text: str, parsed_at: datetime
    ) -> Resume | None:
        record = await self._session.get(ResumeRecord, resume_id)
        if record is None:
            return None
        record.raw_text = raw_text
        record.status = ResumeStatus.PARSED
        record.parsed_at = parsed_at
        record.parse_error = None
        await self._session.flush()
        return _to_resume(record)

    async def mark_parse_failed(self, resume_id: ResumeId, parse_error: str) -> Resume | None:
        record = await self._session.get(ResumeRecord, resume_id)
        if record is None:
            return None
        record.status = ResumeStatus.PARSE_FAILED
        record.parse_error = parse_error
        await self._session.flush()
        return _to_resume(record)

    async def archive(self, resume_id: ResumeId) -> Resume | None:
        return await self.set_status(resume_id, ResumeStatus.ARCHIVED)


class CandidateProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, profile_id: ProfileId) -> CandidateProfile | None:
        record = await self._session.get(CandidateProfileRecord, profile_id)
        return _to_candidate_profile(record) if record is not None else None

    async def get_by_resume(self, resume_id: ResumeId) -> CandidateProfile | None:
        record = await self._session.scalar(
            select(CandidateProfileRecord).where(CandidateProfileRecord.resume_id == resume_id)
        )
        return _to_candidate_profile(record) if record is not None else None

    async def list_for_user(self, user_id: UserId) -> list[CandidateProfile]:
        result = await self._session.scalars(
            select(CandidateProfileRecord)
            .where(CandidateProfileRecord.user_id == user_id)
            .order_by(CandidateProfileRecord.generated_at.desc())
        )
        return [_to_candidate_profile(record) for record in result]

    async def add(self, profile: CandidateProfile) -> CandidateProfile:
        self._session.add(
            CandidateProfileRecord(
                id=profile.id,
                user_id=profile.user_id,
                resume_id=profile.resume_id,
                title=profile.title,
                summary=profile.summary,
                skills=profile.skills,
                experience_years=profile.experience_years,
                seniority=profile.seniority,
                education=[entry.model_dump() for entry in profile.education],
                certifications=profile.certifications,
                projects=profile.projects,
                industries=profile.industries,
                target_roles=profile.target_roles,
                status=profile.status,
                generated_at=profile.generated_at,
            )
        )
        await self._session.flush()
        return profile

    async def archive_for_resume(self, resume_id: ResumeId) -> CandidateProfile | None:
        record = await self._session.scalar(
            select(CandidateProfileRecord).where(CandidateProfileRecord.resume_id == resume_id)
        )
        if record is None:
            return None
        record.status = ProfileStatus.ARCHIVED
        await self._session.flush()
        return _to_candidate_profile(record)


__all__ = [
    "CandidateProfileRepository",
    "ResumeRepository",
    "to_resume_profile",
]
