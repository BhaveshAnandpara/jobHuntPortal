"""Tests for `tracking.consumers` — all seven Kafka consumer handlers.

Scenario letters below match the task brief's minimum-required test list
(kept in docstrings/comments so they stay traceable):
    A - jobs.discovered creates/updates tracking state
    B - jobs.matched advances state (SHORTLIST/BORDERLINE->MATCHED,
        IGNORE->IGNORED)
    C - jobs.shortlisted advances state (both SHORTLISTED and
        CONTACT_SEARCH history rows land)
    D - duplicate Kafka event -> no duplicate history row/transition/publish
    J - applications.updated event correctness (event-triggered half; the
        manual-API half is in test_api.py)
    (plus the explicit out-of-order arrival test required by the brief)

Most tests exercise the `_handle_x_async` functions directly (awaited
in-loop) rather than the outer sync `handle_x` — mirrors
`tests/matching/test_consumers.py`'s documented rationale: the outer sync
wrapper's only job is `asyncio.run(...)`, which cannot itself be called
from inside an already-running event loop (i.e. from inside an
`@pytest.mark.asyncio` test). The sync wrapper gets its own dedicated test
below with the async body replaced by a spy.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import tracking.consumers as consumers_module
import tracking.db as db_module
import tracking.events as events_module
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import build_envelope, deserialize
from infrastructure.kafka.topics import Topic
from shared.events.payloads import OutreachDecision, OutreachSentConfirmation
from shared.types.dto import OutreachDraft
from shared.types.enums import (
    ApplicationStatus,
    MatchRecommendation,
    OutreachChannel,
    OutreachDecisionType,
)
from shared.types.ids import ContactId, CorrelationId, JobId, OutreachId, UserId
from tests.tracking.conftest import (
    make_contact_ranking_result,
    make_job_match_result,
    make_normalized_job,
    make_ranked_contact,
    now,
)
from tracking.repository import ApplicationHistoryRepository, ApplicationRepository


def _wire(session_factory: async_sessionmaker[AsyncSession], broker: InMemoryBroker) -> None:
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("tracking-service", client=InMemoryProducerClient(broker))
    )


async def _get_application(session_factory, job_id: JobId):
    async with session_factory() as session:
        return await ApplicationRepository(session).get_for_job(job_id)


async def _get_history(session_factory, application_id):
    async with session_factory() as session:
        return await ApplicationHistoryRepository(session).list_for_application(application_id)


# ---------------------------------------------------------------------------
# Scenario A — jobs.discovered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_job_discovered_creates_application_at_discovered(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()
    _wire(session_factory, broker)

    envelope = build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")

    await consumers_module._handle_job_discovered_async(envelope)

    application = await _get_application(session_factory, job.job_id)
    assert application is not None
    assert application.status == ApplicationStatus.DISCOVERED
    assert application.company == job.company
    assert application.title == job.title

    history = await _get_history(session_factory, application.id)
    assert len(history) == 1
    assert history[0].from_status is None
    assert history[0].to_status == ApplicationStatus.DISCOVERED
    assert history[0].triggered_by == "job-ingestion-service"

    messages = broker.log(Topic.APPLICATIONS_UPDATED.value)
    assert len(messages) == 1
    published = deserialize(Topic.APPLICATIONS_UPDATED, messages[0].value())
    assert published.payload.previous_status is None
    assert published.payload.new_status == ApplicationStatus.DISCOVERED


# ---------------------------------------------------------------------------
# Scenario B — jobs.matched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recommendation", [MatchRecommendation.SHORTLIST, MatchRecommendation.BORDERLINE]
)
async def test_handle_job_matched_advances_to_matched(
    session_factory: async_sessionmaker[AsyncSession], recommendation: MatchRecommendation
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()
    _wire(session_factory, broker)

    discovered_envelope = build_envelope(
        Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service"
    )
    await consumers_module._handle_job_discovered_async(discovered_envelope)

    result = make_job_match_result(job.job_id, user_id, recommendation=recommendation)
    matched_envelope = build_envelope(Topic.JOBS_MATCHED, result, producer="job-matching-service")
    await consumers_module._handle_job_matched_async(matched_envelope)

    application = await _get_application(session_factory, job.job_id)
    assert application is not None
    assert application.status == ApplicationStatus.MATCHED
    assert application.selected_resume_id == result.selected_resume_id
    assert application.match_score == pytest.approx(result.match_score)
    assert application.matched_skills == result.matched_skills
    assert application.missing_skills == result.missing_skills

    history = await _get_history(session_factory, application.id)
    assert len(history) == 2
    assert history[1].from_status == ApplicationStatus.DISCOVERED
    assert history[1].to_status == ApplicationStatus.MATCHED

    assert len(broker.log(Topic.APPLICATIONS_UPDATED.value)) == 2


@pytest.mark.asyncio
async def test_handle_job_matched_advances_to_ignored_for_ignore_recommendation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()
    _wire(session_factory, broker)

    await consumers_module._handle_job_discovered_async(
        build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")
    )

    result = make_job_match_result(job.job_id, user_id, recommendation=MatchRecommendation.IGNORE)
    await consumers_module._handle_job_matched_async(
        build_envelope(Topic.JOBS_MATCHED, result, producer="job-matching-service")
    )

    application = await _get_application(session_factory, job.job_id)
    assert application is not None
    assert application.status == ApplicationStatus.IGNORED

    history = await _get_history(session_factory, application.id)
    assert history[-1].to_status == ApplicationStatus.IGNORED


# ---------------------------------------------------------------------------
# Scenario C — jobs.shortlisted (two history rows, one handler call)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_job_shortlisted_records_shortlisted_then_contact_search(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()
    _wire(session_factory, broker)

    await consumers_module._handle_job_discovered_async(
        build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")
    )
    result = make_job_match_result(job.job_id, user_id, recommendation=MatchRecommendation.SHORTLIST)
    await consumers_module._handle_job_matched_async(
        build_envelope(Topic.JOBS_MATCHED, result, producer="job-matching-service")
    )

    await consumers_module._handle_job_shortlisted_async(
        build_envelope(Topic.JOBS_SHORTLISTED, result, producer="job-matching-service")
    )

    application = await _get_application(session_factory, job.job_id)
    assert application is not None
    assert application.status == ApplicationStatus.CONTACT_SEARCH

    history = await _get_history(session_factory, application.id)
    # DISCOVERED -> MATCHED -> SHORTLISTED -> CONTACT_SEARCH
    assert [h.to_status for h in history] == [
        ApplicationStatus.DISCOVERED,
        ApplicationStatus.MATCHED,
        ApplicationStatus.SHORTLISTED,
        ApplicationStatus.CONTACT_SEARCH,
    ]

    # 1 (discovered) + 1 (matched) + 2 (shortlisted, contact_search)
    assert len(broker.log(Topic.APPLICATIONS_UPDATED.value)) == 4


# ---------------------------------------------------------------------------
# Scenario D — duplicate delivery is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_jobs_discovered_event_creates_no_duplicate_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()
    _wire(session_factory, broker)

    envelope = build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")
    await consumers_module._handle_job_discovered_async(envelope)
    await consumers_module._handle_job_discovered_async(envelope)  # redelivery

    application = await _get_application(session_factory, job.job_id)
    assert application is not None
    history = await _get_history(session_factory, application.id)
    assert len(history) == 1
    assert len(broker.log(Topic.APPLICATIONS_UPDATED.value)) == 1


@pytest.mark.asyncio
async def test_duplicate_jobs_matched_event_creates_no_duplicate_transition(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()
    _wire(session_factory, broker)

    await consumers_module._handle_job_discovered_async(
        build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")
    )
    result = make_job_match_result(job.job_id, user_id, recommendation=MatchRecommendation.SHORTLIST)
    matched_envelope = build_envelope(Topic.JOBS_MATCHED, result, producer="job-matching-service")

    await consumers_module._handle_job_matched_async(matched_envelope)
    await consumers_module._handle_job_matched_async(matched_envelope)  # redelivery

    application = await _get_application(session_factory, job.job_id)
    assert application is not None
    assert application.status == ApplicationStatus.MATCHED
    history = await _get_history(session_factory, application.id)
    assert len(history) == 2  # discovered + matched, not 3
    assert len(broker.log(Topic.APPLICATIONS_UPDATED.value)) == 2


# ---------------------------------------------------------------------------
# Out-of-order arrival (jobs.matched before jobs.discovered)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jobs_matched_before_jobs_discovered_creates_application_and_later_backfills(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()
    _wire(session_factory, broker)

    result = make_job_match_result(job.job_id, user_id, recommendation=MatchRecommendation.SHORTLIST)
    await consumers_module._handle_job_matched_async(
        build_envelope(Topic.JOBS_MATCHED, result, producer="job-matching-service")
    )

    application = await _get_application(session_factory, job.job_id)
    assert application is not None
    assert application.status == ApplicationStatus.MATCHED
    assert application.company == ""  # no NormalizedJob seen yet
    assert application.title == ""

    history = await _get_history(session_factory, application.id)
    assert len(history) == 1
    assert history[0].from_status is None
    assert history[0].to_status == ApplicationStatus.MATCHED
    assert len(broker.log(Topic.APPLICATIONS_UPDATED.value)) == 1

    # jobs.discovered now arrives late: must not regress status, must
    # backfill company/title, must not create a duplicate history row or
    # publish (not forward progress by rank).
    await consumers_module._handle_job_discovered_async(
        build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")
    )

    application = await _get_application(session_factory, job.job_id)
    assert application is not None
    assert application.status == ApplicationStatus.MATCHED  # unchanged
    assert application.company == job.company  # backfilled
    assert application.title == job.title  # backfilled

    history = await _get_history(session_factory, application.id)
    assert len(history) == 1  # no new row from the backfill
    assert len(broker.log(Topic.APPLICATIONS_UPDATED.value)) == 1  # no new publish


# ---------------------------------------------------------------------------
# contacts.found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_contacts_found_with_contacts_sets_referral_and_advances(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()
    _wire(session_factory, broker)

    await consumers_module._handle_job_discovered_async(
        build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")
    )
    result = make_job_match_result(job.job_id, user_id, recommendation=MatchRecommendation.SHORTLIST)
    await consumers_module._handle_job_matched_async(
        build_envelope(Topic.JOBS_MATCHED, result, producer="job-matching-service")
    )
    await consumers_module._handle_job_shortlisted_async(
        build_envelope(Topic.JOBS_SHORTLISTED, result, producer="job-matching-service")
    )

    top_contact = make_ranked_contact(relevance_score=9.2)
    ranking = make_contact_ranking_result(job.job_id, user_id, contacts=[top_contact])
    await consumers_module._handle_contacts_found_async(
        build_envelope(Topic.CONTACTS_FOUND, ranking, producer="contact-discovery-service")
    )

    application = await _get_application(session_factory, job.job_id)
    assert application is not None
    assert application.status == ApplicationStatus.CONTACT_FOUND
    assert application.referral_contact_id == top_contact.contact_id


@pytest.mark.asyncio
async def test_handle_contacts_found_empty_when_already_at_contact_search_is_noop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()
    _wire(session_factory, broker)

    await consumers_module._handle_job_discovered_async(
        build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")
    )
    result = make_job_match_result(job.job_id, user_id, recommendation=MatchRecommendation.SHORTLIST)
    await consumers_module._handle_job_matched_async(
        build_envelope(Topic.JOBS_MATCHED, result, producer="job-matching-service")
    )
    await consumers_module._handle_job_shortlisted_async(
        build_envelope(Topic.JOBS_SHORTLISTED, result, producer="job-matching-service")
    )
    published_before = len(broker.log(Topic.APPLICATIONS_UPDATED.value))

    application_before = await _get_application(session_factory, job.job_id)
    history_before = await _get_history(session_factory, application_before.id)

    empty_ranking = make_contact_ranking_result(job.job_id, user_id, contacts=[])
    await consumers_module._handle_contacts_found_async(
        build_envelope(Topic.CONTACTS_FOUND, empty_ranking, producer="contact-discovery-service")
    )

    application_after = await _get_application(session_factory, job.job_id)
    assert application_after.status == ApplicationStatus.CONTACT_SEARCH  # unchanged
    history_after = await _get_history(session_factory, application_after.id)
    assert len(history_after) == len(history_before)  # no new row
    assert len(broker.log(Topic.APPLICATIONS_UPDATED.value)) == published_before  # no new publish


@pytest.mark.asyncio
async def test_handle_contacts_found_empty_creates_application_when_missing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Out-of-order: contacts.found (empty) arrives before jobs.discovered
    and before any Application row exists — this *is* a genuine
    previous_status=None -> CONTACT_SEARCH creation, so it publishes,
    unlike the already-exists no-op case above (see consumers.py's
    module docstring's judgment-call note)."""
    user_id = UserId(uuid4())
    job_id = JobId(uuid4())
    broker = InMemoryBroker()
    _wire(session_factory, broker)

    empty_ranking = make_contact_ranking_result(job_id, user_id, contacts=[])
    await consumers_module._handle_contacts_found_async(
        build_envelope(Topic.CONTACTS_FOUND, empty_ranking, producer="contact-discovery-service")
    )

    application = await _get_application(session_factory, job_id)
    assert application is not None
    assert application.status == ApplicationStatus.CONTACT_SEARCH
    history = await _get_history(session_factory, application.id)
    assert len(history) == 1
    assert history[0].from_status is None
    assert len(broker.log(Topic.APPLICATIONS_UPDATED.value)) == 1


# ---------------------------------------------------------------------------
# outreach.generated / outreach.approved / outreach.sent
# ---------------------------------------------------------------------------


async def _shortlist_and_find_contact(session_factory, broker, user_id, job):
    await consumers_module._handle_job_discovered_async(
        build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")
    )
    result = make_job_match_result(job.job_id, user_id, recommendation=MatchRecommendation.SHORTLIST)
    await consumers_module._handle_job_matched_async(
        build_envelope(Topic.JOBS_MATCHED, result, producer="job-matching-service")
    )
    await consumers_module._handle_job_shortlisted_async(
        build_envelope(Topic.JOBS_SHORTLISTED, result, producer="job-matching-service")
    )
    ranking = make_contact_ranking_result(job.job_id, user_id, contacts=[make_ranked_contact()])
    await consumers_module._handle_contacts_found_async(
        build_envelope(Topic.CONTACTS_FOUND, ranking, producer="contact-discovery-service")
    )


@pytest.mark.asyncio
async def test_handle_outreach_generated_advances_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()
    _wire(session_factory, broker)
    await _shortlist_and_find_contact(session_factory, broker, user_id, job)

    draft = OutreachDraft(
        outreach_id=OutreachId(uuid4()),
        job_id=job.job_id,
        contact_id=ContactId(uuid4()),
        user_id=user_id,
        channel=OutreachChannel.EMAIL,
        draft_message="Hi there!",
        generated_at=now(),
    )
    await consumers_module._handle_outreach_generated_async(
        build_envelope(Topic.OUTREACH_GENERATED, draft, producer="outreach-service")
    )

    application = await _get_application(session_factory, job.job_id)
    assert application.status == ApplicationStatus.OUTREACH_GENERATED


@pytest.mark.asyncio
async def test_handle_outreach_approved_advances_status_when_approved(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()
    _wire(session_factory, broker)
    await _shortlist_and_find_contact(session_factory, broker, user_id, job)
    draft = OutreachDraft(
        outreach_id=OutreachId(uuid4()),
        job_id=job.job_id,
        contact_id=ContactId(uuid4()),
        user_id=user_id,
        channel=OutreachChannel.EMAIL,
        draft_message="Hi there!",
        generated_at=now(),
    )
    await consumers_module._handle_outreach_generated_async(
        build_envelope(Topic.OUTREACH_GENERATED, draft, producer="outreach-service")
    )

    decision = OutreachDecision(
        outreach_id=draft.outreach_id,
        job_id=job.job_id,
        user_id=user_id,
        decision=OutreachDecisionType.APPROVED,
        decided_at=now(),
        decided_by=user_id,
    )
    await consumers_module._handle_outreach_approved_async(
        build_envelope(Topic.OUTREACH_APPROVED, decision, producer="outreach-service")
    )

    application = await _get_application(session_factory, job.job_id)
    assert application.status == ApplicationStatus.OUTREACH_APPROVED


@pytest.mark.asyncio
async def test_handle_outreach_approved_noop_when_decision_not_approved(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()
    _wire(session_factory, broker)
    await _shortlist_and_find_contact(session_factory, broker, user_id, job)
    draft_outreach_id = OutreachId(uuid4())

    decision = OutreachDecision(
        outreach_id=draft_outreach_id,
        job_id=job.job_id,
        user_id=user_id,
        decision=OutreachDecisionType.REJECTED,
        decided_at=now(),
        decided_by=user_id,
    )
    published_before = len(broker.log(Topic.APPLICATIONS_UPDATED.value))
    await consumers_module._handle_outreach_approved_async(
        build_envelope(Topic.OUTREACH_APPROVED, decision, producer="outreach-service")
    )

    application = await _get_application(session_factory, job.job_id)
    assert application.status == ApplicationStatus.CONTACT_FOUND  # unchanged
    assert len(broker.log(Topic.APPLICATIONS_UPDATED.value)) == published_before


@pytest.mark.asyncio
async def test_handle_outreach_sent_advances_status(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()
    _wire(session_factory, broker)
    await _shortlist_and_find_contact(session_factory, broker, user_id, job)
    outreach_id = OutreachId(uuid4())
    draft = OutreachDraft(
        outreach_id=outreach_id,
        job_id=job.job_id,
        contact_id=ContactId(uuid4()),
        user_id=user_id,
        channel=OutreachChannel.EMAIL,
        draft_message="Hi there!",
        generated_at=now(),
    )
    await consumers_module._handle_outreach_generated_async(
        build_envelope(Topic.OUTREACH_GENERATED, draft, producer="outreach-service")
    )
    decision = OutreachDecision(
        outreach_id=outreach_id,
        job_id=job.job_id,
        user_id=user_id,
        decision=OutreachDecisionType.APPROVED,
        decided_at=now(),
        decided_by=user_id,
    )
    await consumers_module._handle_outreach_approved_async(
        build_envelope(Topic.OUTREACH_APPROVED, decision, producer="outreach-service")
    )

    confirmation = OutreachSentConfirmation(
        outreach_id=outreach_id,
        job_id=job.job_id,
        user_id=user_id,
        channel=OutreachChannel.EMAIL,
        sent_at=now(),
    )
    await consumers_module._handle_outreach_sent_async(
        build_envelope(Topic.OUTREACH_SENT, confirmation, producer="outreach-service")
    )

    application = await _get_application(session_factory, job.job_id)
    assert application.status == ApplicationStatus.OUTREACH_SENT


# ---------------------------------------------------------------------------
# Terminal-state guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_status_blocks_further_event_driven_transitions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()
    _wire(session_factory, broker)

    await consumers_module._handle_job_discovered_async(
        build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")
    )
    ignore_result = make_job_match_result(
        job.job_id, user_id, recommendation=MatchRecommendation.IGNORE
    )
    await consumers_module._handle_job_matched_async(
        build_envelope(Topic.JOBS_MATCHED, ignore_result, producer="job-matching-service")
    )
    application = await _get_application(session_factory, job.job_id)
    assert application.status == ApplicationStatus.IGNORED
    published_before = len(broker.log(Topic.APPLICATIONS_UPDATED.value))

    # Defensive: even a (architecturally-unexpected) later contacts.found
    # for this job_id must not resurrect a terminal Application.
    ranking = make_contact_ranking_result(job.job_id, user_id, contacts=[make_ranked_contact()])
    await consumers_module._handle_contacts_found_async(
        build_envelope(Topic.CONTACTS_FOUND, ranking, producer="contact-discovery-service")
    )

    application_after = await _get_application(session_factory, job.job_id)
    assert application_after.status == ApplicationStatus.IGNORED
    assert len(broker.log(Topic.APPLICATIONS_UPDATED.value)) == published_before


# ---------------------------------------------------------------------------
# Scenario J — correlation_id propagation (event-triggered half)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correlation_id_is_propagated_unchanged_on_event_triggered_publish(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    broker = InMemoryBroker()
    _wire(session_factory, broker)
    correlation_id = CorrelationId(uuid4())

    envelope = build_envelope(
        Topic.JOBS_DISCOVERED,
        job,
        producer="job-ingestion-service",
        correlation_id=correlation_id,
    )
    await consumers_module._handle_job_discovered_async(envelope)

    published = deserialize(
        Topic.APPLICATIONS_UPDATED, broker.log(Topic.APPLICATIONS_UPDATED.value)[0].value()
    )
    assert published.correlation_id == correlation_id


# ---------------------------------------------------------------------------
# Sync wrapper
# ---------------------------------------------------------------------------


def test_handle_job_discovered_sync_wrapper_drives_the_async_body(monkeypatch) -> None:
    calls: list[object] = []

    async def fake_async_handler(envelope: object) -> None:
        calls.append(envelope)

    monkeypatch.setattr(consumers_module, "_handle_job_discovered_async", fake_async_handler)

    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    envelope = build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")

    consumers_module.handle_job_discovered(envelope)

    assert calls == [envelope]
