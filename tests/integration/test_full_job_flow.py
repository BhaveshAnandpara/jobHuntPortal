"""Primary end-to-end scenario: resume upload -> profile -> job ingestion ->
matching -> contact discovery -> outreach generation -> human approval ->
send -> tracking reflects the full lifecycle.

See docs/architecture/overview.md's Event Flow diagram and
.claude/agents/integration-agent.md's Required Validation list 1-11.
"""

from __future__ import annotations

import pytest

from infrastructure.kafka.topics import Topic
from shared.types.enums import (
    ApplicationStatus,
    ContactType,
    MatchRecommendation,
)
from tests.contacts.conftest import make_hit
from tests.integration.conftest import (
    ContactsFakeLLMClient,
    FakeUserPreferencesClient,
    IntegrationHarness,
    MatchingFakeLLMClient,
    OutreachFakeLLMClient,
    dispatch,
    drain_chain,
    llm_response,
    log_envelopes,
    snapshot_offsets,
)
from workflows.langgraph.contact_discovery.discovery import (
    ContactClassificationBatch,
    ContactSearchPlan,
    HitClassification,
    RelevanceSignals,
)
from workflows.langgraph.job_matching.scoring import ProfileScoringOutput
from workflows.langgraph.outreach_generation.generation import OutreachDraftContent

JOB_URL = "https://boards.example.com/jobs/mech-1"

_RESUME_TEXT = (
    "Jordan Rivera. Senior Mechanical Design Engineer. 7 years designing "
    "mechanical subsystems for industrial robots. Skills: CAD, SolidWorks, "
    "GD&T, DFM, tolerance stack-up analysis. Led design reviews and "
    "prototype validation for three product launches."
)

_EXTRACTED_PROFILE_FIELDS = {
    "title": "Senior Mechanical Design Engineer",
    "summary": "Mechanical engineer with 7 years designing robotic subsystems.",
    "skills": ["CAD", "SolidWorks", "GD&T", "DFM", "Tolerance Stack-up"],
    "experience_years": 7.0,
    "seniority": "Senior",
    "education": [],
    "certifications": [],
    "projects": ["Industrial robot arm redesign"],
    "industries": ["Robotics"],
    "target_roles": ["Mechanical Design Engineer"],
}

_JOB_PAGE_TEXT = (
    "Senior Mechanical Design Engineer at Acme Robotics. Remote. "
    "Own CAD models from concept through DFM/DFA review, run tolerance "
    "stack-ups, and validate designs with prototype testing. Requires "
    "SolidWorks, GD&T, DFM."
)


def _extracted_job_fields():
    from tests.jobs.conftest import make_extracted_fields

    return make_extracted_fields(
        company="Acme Robotics",
        title="Senior Mechanical Design Engineer",
        location="Remote",
        description=_JOB_PAGE_TEXT,
        extracted_skills=["CAD", "SolidWorks", "GD&T", "DFM"],
        experience_required="5+ years",
    )


@pytest.mark.asyncio
async def test_full_job_flow_end_to_end(harness: IntegrationHarness) -> None:
    # 1. User + resume upload -> profile generated -----------------------------
    user = harness.create_user()
    harness.set_profiles_llm([llm_response(_EXTRACTED_PROFILE_FIELDS)])
    resume = harness.upload_resume(user["id"], "resume.txt", _RESUME_TEXT)

    profiles = harness.list_profiles(user["id"])
    assert len(profiles) == 1
    assert profiles[0]["title"] == "Senior Mechanical Design Engineer"

    # 2. Manual job URL -> jobs.discovered ---------------------------------------
    harness.set_job_ingestion_fakes(
        pages={JOB_URL: _JOB_PAGE_TEXT},
        extractor_by_content={_JOB_PAGE_TEXT: _extracted_job_fields()},
    )
    job = harness.ingest_job(user["id"], JOB_URL)
    assert job["processing_status"] == "NORMALIZED"
    job_id = job["id"]

    discovered = log_envelopes(harness.broker, Topic.JOBS_DISCOVERED)
    assert len(discovered) == 1
    assert str(discovered[0].payload.job_id) == job_id
    correlation_id = discovered[0].correlation_id

    # 3. Wire matching's profile/preferences/LLM fakes ---------------------------
    harness.sync_matching_profiles(user["id"])
    harness.set_matching_preferences(FakeUserPreferencesClient({}))
    harness.set_matching_llm(
        MatchingFakeLLMClient(
            default=ProfileScoringOutput(
                role_relevance=0.95,
                skills_fit=0.95,
                experience_fit=0.9,
                domain_fit=0.9,
                seniority_fit=0.9,
                location_preference_fit=0.5,
                overall_score=0.93,
                matched_skills=["CAD", "SolidWorks", "GD&T", "DFM"],
                missing_skills=[],
                reasoning="Strong mechanical design fit.",
            )
        )
    )

    # 4. Wire contact discovery's people-search + LLM fakes ----------------------
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
                "Propose 3-6 short search": ContactSearchPlan(
                    role_keywords=["Mechanical Design Engineer", "Engineering Manager"]
                ),
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

    # 5. Wire outreach generation's LLM fake --------------------------------------
    harness.set_outreach_llm(
        OutreachFakeLLMClient(
            default=OutreachDraftContent(
                body=(
                    "Hi Sam, I saw the Senior Mechanical Design Engineer opening "
                    "at Acme Robotics. I've spent 7 years designing robotic "
                    "subsystems with SolidWorks and GD&T-driven DFM reviews, and "
                    "would love to connect about the role."
                )
            )
        )
    )

    # --- Drive jobs.discovered -> jobs.matched/jobs.shortlisted/contacts.requested
    #     -> contacts.found -------------------------------------------------------
    # Driven by hand (not drain_chain) up through contacts.requested, because
    # outreach's runtime-API fakes (sync_outreach_clients) can only be built
    # from a real JobMatch, which doesn't exist until jobs.discovered has
    # actually been processed.
    discovered_envelope = log_envelopes(harness.broker, Topic.JOBS_DISCOVERED)[0]
    await dispatch(Topic.JOBS_DISCOVERED, discovered_envelope)

    matched = log_envelopes(harness.broker, Topic.JOBS_MATCHED)
    assert len(matched) == 1
    assert matched[0].payload.recommendation == MatchRecommendation.SHORTLIST
    assert matched[0].correlation_id == correlation_id
    await dispatch(Topic.JOBS_MATCHED, matched[0])

    shortlisted = log_envelopes(harness.broker, Topic.JOBS_SHORTLISTED)
    assert len(shortlisted) == 1
    await dispatch(Topic.JOBS_SHORTLISTED, shortlisted[0])

    contacts_requested = log_envelopes(harness.broker, Topic.CONTACTS_REQUESTED)
    assert len(contacts_requested) == 1
    assert contacts_requested[0].payload.company == "Acme Robotics"
    await dispatch(Topic.CONTACTS_REQUESTED, contacts_requested[0])

    contacts_found = log_envelopes(harness.broker, Topic.CONTACTS_FOUND)
    assert len(contacts_found) == 1
    assert len(contacts_found[0].payload.contacts) == 1
    assert contacts_found[0].payload.contacts[0].full_name == "Sam Lee"

    # Now that jobs.matched has actually happened, sync outreach's runtime-API
    # fakes from the real JobMatch/Job/Profile before contacts.found reaches
    # Outreach Service's consumer.
    harness.sync_outreach_clients(job_id)
    await dispatch(Topic.CONTACTS_FOUND, contacts_found[0])

    generated = log_envelopes(harness.broker, Topic.OUTREACH_GENERATED)
    assert len(generated) == 1
    outreach_id = str(generated[0].payload.outreach_id)
    await dispatch(Topic.OUTREACH_GENERATED, generated[0])

    offsets = snapshot_offsets(harness.broker)

    # 6. Human approval via the real API ------------------------------------------
    outreach_response = harness.client.get(f"/outreach/{outreach_id}")
    assert outreach_response.status_code == 200
    assert outreach_response.json()["status"] == "PENDING_APPROVAL"

    approved = harness.approve_outreach(outreach_id)
    assert approved["status"] == "APPROVED"

    approved_events = log_envelopes(harness.broker, Topic.OUTREACH_APPROVED)
    assert len(approved_events) == 1
    approval_correlation_id = approved_events[0].correlation_id
    # New causal-chain origin per event-contracts.md's OutreachApprovedEvent
    # note — the approval mints its own correlation_id, not the original.
    assert approval_correlation_id != correlation_id

    # 7. Drive outreach.approved -> send worker + tracking, then outreach.sent ---
    offsets = await drain_chain(harness.broker, offsets=offsets)

    sent = log_envelopes(harness.broker, Topic.OUTREACH_SENT)
    assert len(sent) == 1
    assert sent[0].correlation_id == approval_correlation_id

    outreach_after_send = harness.client.get(f"/outreach/{outreach_id}").json()
    assert outreach_after_send["status"] == "SENT"

    # 8. Tracking reflects the full lifecycle -------------------------------------
    applications = harness.client.get(
        "/applications", params={"user_id": user["id"]}
    ).json()
    assert len(applications) == 1
    application_id = applications[0]["id"]
    assert applications[0]["status"] == ApplicationStatus.OUTREACH_SENT.value

    application = harness.client.get(f"/applications/{application_id}").json()
    assert application["job_id"] == job_id
    assert application["selected_resume_id"] == resume["id"]
    assert application["match_score"] == 0.93
    assert application["referral_contact_id"] is not None

    history = harness.client.get(f"/applications/{application_id}/history").json()
    to_statuses = [entry["to_status"] for entry in history]
    assert to_statuses == [
        ApplicationStatus.DISCOVERED.value,
        ApplicationStatus.MATCHED.value,
        ApplicationStatus.SHORTLISTED.value,
        ApplicationStatus.CONTACT_SEARCH.value,
        ApplicationStatus.CONTACT_FOUND.value,
        ApplicationStatus.OUTREACH_GENERATED.value,
        ApplicationStatus.OUTREACH_APPROVED.value,
        ApplicationStatus.OUTREACH_SENT.value,
    ]
