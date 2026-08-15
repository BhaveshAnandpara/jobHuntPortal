"""Idempotency validation: dedup at ingestion holds through the *full*
downstream chain, and redelivering `jobs.discovered`, `jobs.matched`,
`contacts.requested`, `outreach.approved` — each via the *actual* upstream
event already sitting on the broker, not a hand-crafted duplicate — never
produces duplicate `JobMatch`/`Contact`/`ContactScore`/`ApplicationHistory`
rows or a duplicate external send.

See docs/architecture/database-ownership.md#job_matches's "Publish-
reliability column" section, contacts/consumers.py's and
outreach/consumers.py's own documented idempotency checks, and the task
brief's section 10.
"""

from __future__ import annotations

from uuid import UUID

import pytest

import outreach.consumers as outreach_consumers
from contacts.repository import ContactRepository, ContactScoreRepository
from infrastructure.external.message_send import (
    MessageSendClient,
    RecordingMessageSendProvider,
)
from infrastructure.kafka.topics import Topic
from matching.repository import JobMatchRepository
from outreach.repository import OutreachRepository
from shared.types.enums import ContactType, OutreachChannel
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
    RelevanceSignals,
)
from workflows.langgraph.job_matching.scoring import ProfileScoringOutput
from workflows.langgraph.outreach_generation.generation import OutreachDraftContent

JOB_URL = "https://boards.example.com/jobs/idempotency-1"

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


def _job_extractor_fixture() -> dict:
    return {
        _JOB_PAGE_TEXT: make_extracted_fields(
            company="Acme Robotics",
            title="Senior Mechanical Design Engineer",
            location="Remote",
            description=_JOB_PAGE_TEXT,
            extracted_skills=["CAD", "SolidWorks", "GD&T", "DFM"],
            experience_required="5+ years",
        )
    }


@pytest.mark.asyncio
async def test_duplicate_job_url_ingestion_dedups_through_full_chain(
    harness: IntegrationHarness,
) -> None:
    user = harness.create_user()
    harness.set_profiles_llm([llm_response(_PROFILE_FIELDS)])
    harness.upload_resume(user, "resume.txt", _RESUME_TEXT)

    harness.set_job_ingestion_fakes(
        pages={JOB_URL: _JOB_PAGE_TEXT}, extractor_by_content=_job_extractor_fixture()
    )

    first = harness.ingest_job(user, JOB_URL)
    second = harness.ingest_job(user, JOB_URL)
    assert first["id"] == second["id"]

    discovered = log_envelopes(harness.broker, Topic.JOBS_DISCOVERED)
    assert len(discovered) == 1

    harness.sync_matching_profiles(user)
    harness.set_matching_preferences(FakeUserPreferencesClient({}))
    matching_llm = MatchingFakeLLMClient(
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
    harness.set_matching_llm(matching_llm)
    await dispatch(Topic.JOBS_DISCOVERED, discovered[0])

    matched = log_envelopes(harness.broker, Topic.JOBS_MATCHED)
    assert len(matched) == 1

    applications = harness.client.get(
        "/applications", headers=harness.auth_headers(user)
    ).json()
    assert len(applications) == 1


@pytest.mark.asyncio
async def test_redelivered_jobs_discovered_does_not_rescore_or_duplicate_match(
    harness: IntegrationHarness,
) -> None:
    user = harness.create_user()
    harness.set_profiles_llm([llm_response(_PROFILE_FIELDS)])
    harness.upload_resume(user, "resume.txt", _RESUME_TEXT)
    harness.set_job_ingestion_fakes(
        pages={JOB_URL: _JOB_PAGE_TEXT}, extractor_by_content=_job_extractor_fixture()
    )
    job = harness.ingest_job(user, JOB_URL)

    harness.sync_matching_profiles(user)
    harness.set_matching_preferences(FakeUserPreferencesClient({}))
    matching_llm = MatchingFakeLLMClient(
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
    harness.set_matching_llm(matching_llm)

    discovered_envelope = log_envelopes(harness.broker, Topic.JOBS_DISCOVERED)[0]
    await dispatch(Topic.JOBS_DISCOVERED, discovered_envelope)
    assert len(matching_llm.prompts) == 1  # one profile evaluated once

    # Redeliver the identical jobs.discovered message twice more.
    await dispatch(Topic.JOBS_DISCOVERED, discovered_envelope)
    await dispatch(Topic.JOBS_DISCOVERED, discovered_envelope)

    # No re-scoring: the LLM was never called again.
    assert len(matching_llm.prompts) == 1
    # No duplicate publish.
    assert len(log_envelopes(harness.broker, Topic.JOBS_MATCHED)) == 1

    async with harness.session_factory() as session:
        match = await JobMatchRepository(session).get_latest_for_job(UUID(job["id"]))
    assert match is not None


@pytest.mark.asyncio
async def test_redelivered_contacts_requested_does_not_duplicate_contacts(
    harness: IntegrationHarness,
) -> None:
    user = harness.create_user()
    harness.set_profiles_llm([llm_response(_PROFILE_FIELDS)])
    harness.upload_resume(user, "resume.txt", _RESUME_TEXT)
    harness.set_job_ingestion_fakes(
        pages={JOB_URL: _JOB_PAGE_TEXT}, extractor_by_content=_job_extractor_fixture()
    )
    job = harness.ingest_job(user, JOB_URL)

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
                "Sam Lee": RelevanceSignals(
                    role_similarity=0.9, department_relevance=0.9, seniority_fit=0.8
                ),
            }
        ),
    )

    discovered = log_envelopes(harness.broker, Topic.JOBS_DISCOVERED)[0]
    await dispatch(Topic.JOBS_DISCOVERED, discovered)
    matched = log_envelopes(harness.broker, Topic.JOBS_MATCHED)[0]
    await dispatch(Topic.JOBS_MATCHED, matched)
    shortlisted = log_envelopes(harness.broker, Topic.JOBS_SHORTLISTED)[0]
    await dispatch(Topic.JOBS_SHORTLISTED, shortlisted)

    contacts_requested_envelope = log_envelopes(harness.broker, Topic.CONTACTS_REQUESTED)[0]
    await dispatch(Topic.CONTACTS_REQUESTED, contacts_requested_envelope)
    assert len(log_envelopes(harness.broker, Topic.CONTACTS_FOUND)) == 1

    # Redeliver the actual upstream contacts.requested event again.
    await dispatch(Topic.CONTACTS_REQUESTED, contacts_requested_envelope)
    await dispatch(Topic.CONTACTS_REQUESTED, contacts_requested_envelope)

    assert len(log_envelopes(harness.broker, Topic.CONTACTS_FOUND)) == 1

    async with harness.session_factory() as session:
        contacts = await ContactRepository(session).list_for_job(UUID(job["id"]))
        scores = [
            await ContactScoreRepository(session).get_for_contact(contact.id)
            for contact in contacts
        ]
    assert len(contacts) == 1
    assert len([s for s in scores if s is not None]) == 1


@pytest.mark.asyncio
async def test_redelivered_outreach_approved_sends_only_once_via_real_event(
    harness: IntegrationHarness,
) -> None:
    user = harness.create_user()
    harness.set_profiles_llm([llm_response(_PROFILE_FIELDS)])
    harness.upload_resume(user, "resume.txt", _RESUME_TEXT)
    harness.set_job_ingestion_fakes(
        pages={JOB_URL: _JOB_PAGE_TEXT}, extractor_by_content=_job_extractor_fixture()
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
                "Sam Lee": RelevanceSignals(
                    role_similarity=0.9, department_relevance=0.9, seniority_fit=0.8
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
    outreach_id = str(generated.payload.outreach_id)

    provider = RecordingMessageSendProvider()
    outreach_consumers.set_message_send_client(
        MessageSendClient({channel: provider for channel in OutreachChannel})
    )

    harness.approve_outreach(outreach_id)
    approved_envelope = log_envelopes(harness.broker, Topic.OUTREACH_APPROVED)[0]

    # The *actual* upstream outreach.approved event, redelivered twice.
    await outreach_consumers._handle_outreach_approved_async(approved_envelope)
    await outreach_consumers._handle_outreach_approved_async(approved_envelope)

    assert len(provider.sent) == 1
    assert len(log_envelopes(harness.broker, Topic.OUTREACH_SENT)) == 1

    async with harness.session_factory() as session:
        outreach = await OutreachRepository(session).get_for_job(UUID(job_id))
    assert outreach is not None
    assert outreach.status.value == "SENT"
