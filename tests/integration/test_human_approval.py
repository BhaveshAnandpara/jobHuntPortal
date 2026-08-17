"""Human-in-the-loop validation: CLAUDE.md's "external outreach requires
human approval" rule, proven as a real, unbypassable state transition.

A: even directly feeding the send-worker consumer an `outreach.approved`-
   shaped envelope for a still-`PENDING_APPROVAL` row sends nothing and
   changes nothing — approval can only come from the `/approve` API path,
   which is gated on the DB row's actual status.
B: the full real approve path sends and publishes `outreach.sent`.
C: the full real reject path never produces `outreach.approved`/
   `outreach.sent`.
D: redelivering the same `outreach.approved` envelope twice to the send
   worker sends only once.

See docs/architecture/state-machines.md#outreach-lifecycle and the task
brief's section 8.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import outreach.consumers as outreach_consumers
from infrastructure.external.message_send import (
    MessageSendClient,
    RecordingMessageSendProvider,
)
from infrastructure.kafka.serialization import build_envelope
from infrastructure.kafka.topics import Topic
from shared.events.payloads import OutreachDecision
from shared.types.enums import ContactType, OutreachDecisionType
from tests.integration.conftest import (
    ContactsFakeLLMClient,
    FakeUserPreferencesClient,
    IntegrationHarness,
    MatchingFakeLLMClient,
    OutreachFakeLLMClient,
    dispatch,
    llm_response,
    log_envelopes,
    make_hit,
)
from tests.jobs.conftest import make_extracted_fields
from workflows.langgraph.contact_discovery.discovery import (
    ContactClassificationBatch,
    ContactSearchPlan,
    HitClassification,
    RankedRelevanceSignals,
    RelevanceSignalsBatch,
)
from workflows.langgraph.job_matching.scoring import ProfileScoringOutput
from workflows.langgraph.outreach_generation.generation import OutreachDraftContent

JOB_URL = "https://boards.example.com/jobs/human-approval-1"

_RESUME_TEXT = (
    "Jordan Rivera. Senior Mechanical Design Engineer. 7 years designing "
    "mechanical subsystems for industrial robots."
)
_PROFILE_FIELDS = {
    "title": "Senior Mechanical Design Engineer",
    "summary": "Mechanical engineer with 7 years designing robotic subsystems.",
    "skills": ["CAD", "SolidWorks", "GD&T", "DFM"],
    "experience_years": 7.0,
    "seniority": "Senior",
    "target_roles": ["Mechanical Design Engineer"],
}
_JOB_PAGE_TEXT = (
    "Senior Mechanical Design Engineer at Acme Robotics. Own CAD models "
    "from concept through DFM/DFA review."
)


def _recording_send_client() -> tuple[MessageSendClient, RecordingMessageSendProvider]:
    provider = RecordingMessageSendProvider()
    client = MessageSendClient(
        {channel: provider for channel in _ALL_CHANNELS()}
    )
    return client, provider


def _ALL_CHANNELS():
    from shared.types.enums import OutreachChannel

    return list(OutreachChannel)


async def _drive_to_outreach_generated(harness: IntegrationHarness) -> tuple[str, str, str]:
    user = harness.create_user()
    harness.set_profiles_llm([llm_response(_PROFILE_FIELDS)])
    harness.upload_resume(user, "resume.txt", _RESUME_TEXT)

    harness.set_job_ingestion_fakes(
        pages={JOB_URL: _JOB_PAGE_TEXT},
        extractor_by_content={
            _JOB_PAGE_TEXT: make_extracted_fields(
                company="Acme Robotics",
                title="Senior Mechanical Design Engineer",
                location="Remote",
                description=_JOB_PAGE_TEXT,
                extracted_skills=["CAD", "SolidWorks", "GD&T", "DFM"],
                experience_required="5+ years",
            )
        },
    )
    job = harness.ingest_job(user, JOB_URL)
    job_id = job["id"]

    harness.sync_matching_profiles(user)
    harness.set_matching_preferences(FakeUserPreferencesClient({}))
    harness.set_matching_llm(
        MatchingFakeLLMClient(
            default=ProfileScoringOutput(
                role_relevance=0.9,
                skills_fit=0.9,
                experience_fit=0.9,
                domain_fit=0.9,
                seniority_fit=0.9,
                location_preference_fit=0.5,
                overall_score=0.9,
                matched_skills=["CAD", "SolidWorks", "GD&T", "DFM"],
                missing_skills=[],
                reasoning="Strong fit.",
            )
        )
    )
    hit = make_hit(
        full_name="Sam Lee",
        headline="Engineering Manager, Mechanical at Acme Robotics",
        company="Acme Robotics",
        profile_url="https://example.com/in/sam-lee",
    )
    harness.set_contacts_fakes(
        hits=[hit],
        llm=ContactsFakeLLMClient(
            results={
                "Propose 3-6 short search": ContactSearchPlan(role_keywords=["Manager"]),
                "Classify each of the following": ContactClassificationBatch(
                    classifications=[
                        HitClassification(index=0, contact_type=ContactType.HIRING_MANAGER)
                    ]
                ),
                "Score EVERY contact": RelevanceSignalsBatch(
                    signals=[
                        RankedRelevanceSignals(
                            index=0, role_similarity=0.9, department_relevance=0.9, seniority_fit=0.8
                        ),
                    ]
                ),
            }
        ),
    )
    harness.set_outreach_llm(
        OutreachFakeLLMClient(default=OutreachDraftContent(body="Hi Sam, ..."))
    )

    discovered = log_envelopes(harness.broker, Topic.JOBS_DISCOVERED)[0]
    await dispatch(Topic.JOBS_DISCOVERED, discovered)
    matched = log_envelopes(harness.broker, Topic.JOBS_MATCHED)[0]
    await dispatch(Topic.JOBS_MATCHED, matched)
    shortlisted = log_envelopes(harness.broker, Topic.JOBS_SHORTLISTED)[0]
    await dispatch(Topic.JOBS_SHORTLISTED, shortlisted)
    contacts_requested = log_envelopes(harness.broker, Topic.CONTACTS_REQUESTED)[0]
    await dispatch(Topic.CONTACTS_REQUESTED, contacts_requested)
    contacts_found = log_envelopes(harness.broker, Topic.CONTACTS_FOUND)[0]
    harness.sync_outreach_clients(job_id)
    await dispatch(Topic.CONTACTS_FOUND, contacts_found)
    generated = log_envelopes(harness.broker, Topic.OUTREACH_GENERATED)[0]
    await dispatch(Topic.OUTREACH_GENERATED, generated)

    return job_id, str(generated.payload.outreach_id), user["id"]


@pytest.mark.asyncio
async def test_a_send_worker_ignores_a_forged_approval_for_a_pending_row(
    harness: IntegrationHarness,
) -> None:
    _job_id, outreach_id, _user_id = await _drive_to_outreach_generated(harness)

    outreach_before = harness.client.get(f"/outreach/{outreach_id}").json()
    assert outreach_before["status"] == "PENDING_APPROVAL"

    send_client, provider = _recording_send_client()
    outreach_consumers.set_message_send_client(send_client)

    # Simulate "what if a message ended up on outreach.approved without
    # going through /approve" — hand-build the envelope directly, never
    # calling POST /outreach/{id}/approve.
    forged_decision = OutreachDecision(
        outreach_id=outreach_before["id"],
        job_id=outreach_before["job_id"],
        user_id=_user_id,
        decision=OutreachDecisionType.APPROVED,
        final_message=None,
        decided_at=datetime.now(UTC),
        decided_by=_user_id,
    )
    forged_envelope = build_envelope(
        Topic.OUTREACH_APPROVED, forged_decision, producer="attacker-simulation"
    )

    await outreach_consumers._handle_outreach_approved_async(forged_envelope)

    assert provider.sent == []
    outreach_after = harness.client.get(f"/outreach/{outreach_id}").json()
    assert outreach_after["status"] == "PENDING_APPROVAL"
    assert outreach_after["sent_at"] is None


@pytest.mark.asyncio
async def test_b_full_approve_path_sends_and_publishes_outreach_sent(
    harness: IntegrationHarness,
) -> None:
    _job_id, outreach_id, _user_id = await _drive_to_outreach_generated(harness)

    send_client, provider = _recording_send_client()
    outreach_consumers.set_message_send_client(send_client)

    approved = harness.approve_outreach(outreach_id)
    assert approved["status"] == "APPROVED"

    approved_envelope = log_envelopes(harness.broker, Topic.OUTREACH_APPROVED)[0]
    await outreach_consumers._handle_outreach_approved_async(approved_envelope)

    assert len(provider.sent) == 1
    outreach_after = harness.client.get(f"/outreach/{outreach_id}").json()
    assert outreach_after["status"] == "SENT"
    assert outreach_after["sent_at"] is not None

    sent_events = log_envelopes(harness.broker, Topic.OUTREACH_SENT)
    assert len(sent_events) == 1
    assert str(sent_events[0].payload.outreach_id) == outreach_id


@pytest.mark.asyncio
async def test_c_full_reject_path_never_produces_approved_or_sent(
    harness: IntegrationHarness,
) -> None:
    _job_id, outreach_id, _user_id = await _drive_to_outreach_generated(harness)

    rejected = harness.reject_outreach(outreach_id)
    assert rejected["status"] == "REJECTED"

    assert log_envelopes(harness.broker, Topic.OUTREACH_APPROVED) == []
    assert log_envelopes(harness.broker, Topic.OUTREACH_SENT) == []

    outreach_after = harness.client.get(f"/outreach/{outreach_id}").json()
    assert outreach_after["status"] == "REJECTED"
    assert outreach_after["sent_at"] is None


@pytest.mark.asyncio
async def test_d_redelivered_approval_sends_only_once(harness: IntegrationHarness) -> None:
    _job_id, outreach_id, _user_id = await _drive_to_outreach_generated(harness)

    send_client, provider = _recording_send_client()
    outreach_consumers.set_message_send_client(send_client)

    harness.approve_outreach(outreach_id)
    approved_envelope = log_envelopes(harness.broker, Topic.OUTREACH_APPROVED)[0]

    await outreach_consumers._handle_outreach_approved_async(approved_envelope)
    await outreach_consumers._handle_outreach_approved_async(approved_envelope)

    assert len(provider.sent) == 1

    sent_events = log_envelopes(harness.broker, Topic.OUTREACH_SENT)
    assert len(sent_events) == 1
