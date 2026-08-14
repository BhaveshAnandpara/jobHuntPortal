"""Persistence boundary for Tracking Service's owned tables
(`applications`, `application_history`).

No other component may import this module. Repositories take and return
the canonical domain types (`shared.types.domain.application.Application`,
`shared.types.domain.application_history.ApplicationHistory`); the
`ApplicationRecord`/`ApplicationHistoryRecord` SQLAlchemy models in
models.py never leave this module — mirrors `matching/repository.py`'s
`_to_domain`-style projection pattern.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.types.domain.application import Application
from shared.types.domain.application_history import ApplicationHistory
from shared.types.enums import ApplicationStatus
from shared.types.ids import (
    ApplicationHistoryId,
    ApplicationId,
    ContactId,
    CorrelationId,
    JobId,
    ResumeId,
    UserId,
)
from tracking.models import ApplicationHistoryRecord, ApplicationRecord


def _application_to_domain(record: ApplicationRecord) -> Application:
    return Application(
        id=ApplicationId(record.id),
        job_id=JobId(record.job_id),
        user_id=UserId(record.user_id),
        company=record.company,
        title=record.title,
        status=record.status,
        selected_resume_id=(
            ResumeId(record.selected_resume_id) if record.selected_resume_id is not None else None
        ),
        match_score=record.match_score,
        matched_skills=list(record.matched_skills),
        missing_skills=list(record.missing_skills),
        referral_contact_id=(
            ContactId(record.referral_contact_id)
            if record.referral_contact_id is not None
            else None
        ),
        applied_date=record.applied_date,
        follow_up_date=record.follow_up_date,
        notes=record.notes,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _history_to_domain(record: ApplicationHistoryRecord) -> ApplicationHistory:
    return ApplicationHistory(
        id=ApplicationHistoryId(record.id),
        application_id=ApplicationId(record.application_id),
        from_status=record.from_status,
        to_status=record.to_status,
        changed_at=record.changed_at,
        triggered_by=record.triggered_by,
        source_event_type=record.source_event_type,
        correlation_id=(
            CorrelationId(record.correlation_id) if record.correlation_id is not None else None
        ),
    )


class ApplicationRepository:
    """Reads/writes only `applications`. `job_id` is 1:1 (unique) —
    `get_for_job` is the primary lookup every Kafka consumer uses to decide
    create-vs-update.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, application_id: ApplicationId) -> Application | None:
        record = await self._session.get(ApplicationRecord, UUID(str(application_id)))
        return _application_to_domain(record) if record is not None else None

    async def get_for_job(self, job_id: JobId) -> Application | None:
        result = await self._session.scalars(
            select(ApplicationRecord).where(ApplicationRecord.job_id == UUID(str(job_id)))
        )
        record = result.first()
        return _application_to_domain(record) if record is not None else None

    async def add(self, application: Application) -> Application:
        self._session.add(
            ApplicationRecord(
                id=application.id,
                job_id=application.job_id,
                user_id=application.user_id,
                company=application.company,
                title=application.title,
                status=application.status,
                selected_resume_id=application.selected_resume_id,
                match_score=application.match_score,
                matched_skills=list(application.matched_skills),
                missing_skills=list(application.missing_skills),
                referral_contact_id=application.referral_contact_id,
                applied_date=application.applied_date,
                follow_up_date=application.follow_up_date,
                notes=application.notes,
                created_at=application.created_at,
                updated_at=application.updated_at,
            )
        )
        await self._session.flush()
        return application

    async def update(self, application: Application) -> Application:
        record = await self._session.get(ApplicationRecord, UUID(str(application.id)))
        if record is None:
            raise ValueError(f"no Application record with id {application.id}")
        record.company = application.company
        record.title = application.title
        record.status = application.status
        record.selected_resume_id = application.selected_resume_id
        record.match_score = application.match_score
        record.matched_skills = list(application.matched_skills)
        record.missing_skills = list(application.missing_skills)
        record.referral_contact_id = application.referral_contact_id
        record.applied_date = application.applied_date
        record.follow_up_date = application.follow_up_date
        record.notes = application.notes
        record.updated_at = application.updated_at
        await self._session.flush()
        return application

    async def list_for_user(
        self, user_id: UserId, status: ApplicationStatus | None = None
    ) -> list[Application]:
        stmt = select(ApplicationRecord).where(ApplicationRecord.user_id == UUID(str(user_id)))
        if status is not None:
            stmt = stmt.where(ApplicationRecord.status == status)
        stmt = stmt.order_by(ApplicationRecord.created_at.asc())
        result = await self._session.scalars(stmt)
        return [_application_to_domain(record) for record in result.all()]


class ApplicationHistoryRepository:
    """Reads/writes only `application_history`. Insert-only — nobody,
    including Tracking Service itself, ever updates a row once written.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_application(
        self, application_id: ApplicationId
    ) -> list[ApplicationHistory]:
        result = await self._session.scalars(
            select(ApplicationHistoryRecord)
            .where(ApplicationHistoryRecord.application_id == UUID(str(application_id)))
            .order_by(ApplicationHistoryRecord.changed_at.asc())
        )
        return [_history_to_domain(record) for record in result.all()]

    async def add(self, entry: ApplicationHistory) -> ApplicationHistory:
        self._session.add(
            ApplicationHistoryRecord(
                id=entry.id,
                application_id=entry.application_id,
                from_status=entry.from_status,
                to_status=entry.to_status,
                changed_at=entry.changed_at,
                triggered_by=entry.triggered_by,
                source_event_type=entry.source_event_type,
                correlation_id=entry.correlation_id,
            )
        )
        await self._session.flush()
        return entry


__all__ = ["ApplicationHistoryRepository", "ApplicationRepository"]
