"""Tests for `tracking.repository.ApplicationRepository` /
`ApplicationHistoryRepository` against a real (SQLite in-memory) async
SQLAlchemy session — docs/architecture/database-ownership.md#applications
and #application_history.

Uses the `session_factory`/`session` fixtures from conftest.py.

Scenario I (persistence/history ordering): `application_history` rows for a
multi-event sequence come back in chronological order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shared.types.domain.application import Application
from shared.types.domain.application_history import ApplicationHistory
from shared.types.enums import ApplicationStatus
from shared.types.ids import ApplicationHistoryId, ApplicationId, JobId, UserId
from tracking.repository import ApplicationHistoryRepository, ApplicationRepository

pytestmark = pytest.mark.asyncio


def _application(**overrides: object) -> Application:
    now = datetime.now(UTC)
    fields: dict[str, object] = {
        "id": ApplicationId(uuid4()),
        "job_id": JobId(uuid4()),
        "user_id": UserId(uuid4()),
        "company": "Acme Robotics",
        "title": "Senior Mechanical Design Engineer",
        "status": ApplicationStatus.DISCOVERED,
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    return Application(**fields)


async def test_add_persists_application_readable_by_id(session: AsyncSession) -> None:
    repository = ApplicationRepository(session)
    application = _application()

    await repository.add(application)
    await session.commit()

    fetched = await repository.get(application.id)
    assert fetched is not None
    assert fetched.job_id == application.job_id
    assert fetched.company == "Acme Robotics"
    assert fetched.status == ApplicationStatus.DISCOVERED
    assert fetched.matched_skills == []
    assert fetched.missing_skills == []


async def test_get_for_job_returns_none_when_no_application_exists(
    session: AsyncSession,
) -> None:
    repository = ApplicationRepository(session)
    assert await repository.get_for_job(JobId(uuid4())) is None


async def test_get_for_job_finds_the_unique_row(session: AsyncSession) -> None:
    repository = ApplicationRepository(session)
    application = _application()
    await repository.add(application)
    await session.commit()

    fetched = await repository.get_for_job(application.job_id)
    assert fetched is not None
    assert fetched.id == application.id


async def test_update_persists_status_and_field_changes(session: AsyncSession) -> None:
    repository = ApplicationRepository(session)
    application = _application()
    await repository.add(application)
    await session.commit()

    application.status = ApplicationStatus.MATCHED
    application.match_score = 0.91
    application.matched_skills = ["CAD"]
    await repository.update(application)
    await session.commit()

    fetched = await repository.get(application.id)
    assert fetched is not None
    assert fetched.status == ApplicationStatus.MATCHED
    assert fetched.match_score == pytest.approx(0.91)
    assert fetched.matched_skills == ["CAD"]


async def test_list_for_user_filters_by_status(session: AsyncSession) -> None:
    repository = ApplicationRepository(session)
    user_id = UserId(uuid4())
    discovered = _application(user_id=user_id, status=ApplicationStatus.DISCOVERED)
    matched = _application(user_id=user_id, status=ApplicationStatus.MATCHED)
    other_user = _application(status=ApplicationStatus.DISCOVERED)
    await repository.add(discovered)
    await repository.add(matched)
    await repository.add(other_user)
    await session.commit()

    all_for_user = await repository.list_for_user(user_id)
    assert {a.id for a in all_for_user} == {discovered.id, matched.id}

    only_matched = await repository.list_for_user(user_id, ApplicationStatus.MATCHED)
    assert [a.id for a in only_matched] == [matched.id]


# ---------------------------------------------------------------------------
# Scenario I — history ordering
# ---------------------------------------------------------------------------


async def test_history_list_for_application_returns_chronological_order(
    session: AsyncSession,
) -> None:
    app_repo = ApplicationRepository(session)
    history_repo = ApplicationHistoryRepository(session)
    application = _application()
    await app_repo.add(application)
    await session.commit()

    base = datetime.now(UTC)
    sequence = [
        (None, ApplicationStatus.DISCOVERED, base),
        (ApplicationStatus.DISCOVERED, ApplicationStatus.MATCHED, base + timedelta(seconds=1)),
        (ApplicationStatus.MATCHED, ApplicationStatus.SHORTLISTED, base + timedelta(seconds=2)),
        (
            ApplicationStatus.SHORTLISTED,
            ApplicationStatus.CONTACT_SEARCH,
            base + timedelta(seconds=3),
        ),
    ]
    # Insert out of chronological order to prove the repository sorts by
    # changed_at, not by insertion order.
    for from_status, to_status, changed_at in reversed(sequence):
        await history_repo.add(
            ApplicationHistory(
                id=ApplicationHistoryId(uuid4()),
                application_id=application.id,
                from_status=from_status,
                to_status=to_status,
                changed_at=changed_at,
                triggered_by="job-matching-service",
            )
        )
    await session.commit()

    entries = await history_repo.list_for_application(application.id)
    assert [e.to_status for e in entries] == [
        ApplicationStatus.DISCOVERED,
        ApplicationStatus.MATCHED,
        ApplicationStatus.SHORTLISTED,
        ApplicationStatus.CONTACT_SEARCH,
    ]
    assert entries[0].from_status is None
