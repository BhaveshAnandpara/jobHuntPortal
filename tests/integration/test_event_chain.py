"""Kafka flow validation: for every event in the chain, assert it
deserializes to the correct payload type, `event_type` matches the topic
per event-contracts.md's table, `event_version` is set, `user_id` is
correct, and `correlation_id` equals the value minted once at
`jobs.discovered` and propagated unchanged through every downstream event
up to (and including) `outreach.generated` — with the one documented
exception that `outreach.approved` legitimately mints a *fresh*
`correlation_id` (no inbound envelope to propagate from at the `/approve`
HTTP handler), which then propagates unchanged into `outreach.sent`.

See docs/architecture/event-contracts.md and the task brief's section 6.
"""

from __future__ import annotations

import pytest

from infrastructure.kafka.serialization import EVENT_VERSION
from infrastructure.kafka.topics import Topic
from shared.types.enums import ContactType, EventType
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
    make_hit,
    snapshot_offsets,
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

JOB_URL = "https://boards.example.com/jobs/event-chain-1"

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


# Every event type, per event-contracts.md's table (event_type -> Topic).
_EVENT_TYPE_BY_TOPIC = {
    Topic.JOBS_DISCOVERED: EventType.JOB_DISCOVERED,
    Topic.JOBS_MATCHED: EventType.JOB_MATCHED,
    Topic.JOBS_SHORTLISTED: EventType.JOB_SHORTLISTED,
    Topic.CONTACTS_REQUESTED: EventType.CONTACTS_REQUESTED,
    Topic.CONTACTS_FOUND: EventType.CONTACTS_FOUND,
    Topic.OUTREACH_GENERATED: EventType.OUTREACH_GENERATED,
    Topic.OUTREACH_APPROVED: EventType.OUTREACH_APPROVED,
    Topic.OUTREACH_SENT: EventType.OUTREACH_SENT,
}


@pytest.mark.asyncio
async def test_event_chain_contract_shape_and_correlation(
    harness: IntegrationHarness,
) -> None:
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

    discovered_envelope = log_envelopes(harness.broker, Topic.JOBS_DISCOVERED)[0]
    origin_correlation_id = discovered_envelope.correlation_id
    await dispatch(Topic.JOBS_DISCOVERED, discovered_envelope)

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

    # --- Assert contract shape + correlation propagation up through
    #     outreach.generated -----------------------------------------------
    pre_approval_envelopes = {
        Topic.JOBS_DISCOVERED: discovered_envelope,
        Topic.JOBS_MATCHED: matched,
        Topic.JOBS_SHORTLISTED: shortlisted,
        Topic.CONTACTS_REQUESTED: contacts_requested,
        Topic.CONTACTS_FOUND: contacts_found,
        Topic.OUTREACH_GENERATED: generated,
    }
    for topic, envelope in pre_approval_envelopes.items():
        assert envelope.event_type == _EVENT_TYPE_BY_TOPIC[topic], topic
        assert envelope.event_version == EVENT_VERSION
        assert str(envelope.user_id) == user["id"]
        assert envelope.correlation_id == origin_correlation_id, (
            f"{topic} broke correlation_id propagation"
        )
        assert envelope.metadata.producer  # non-empty component name
        assert envelope.metadata.failure_reason is None
        assert envelope.metadata.error_code is None

    outreach_id = str(generated.payload.outreach_id)
    offsets = snapshot_offsets(harness.broker)

    # --- outreach.approved: a legitimate new causal-chain origin ----------------
    harness.approve_outreach(outreach_id)
    approved = log_envelopes(harness.broker, Topic.OUTREACH_APPROVED)[0]
    assert approved.event_type == EventType.OUTREACH_APPROVED
    assert approved.event_version == EVENT_VERSION
    assert str(approved.user_id) == user["id"]
    assert approved.correlation_id != origin_correlation_id, (
        "outreach.approved should mint a fresh correlation_id (no inbound "
        "envelope to propagate from at the /approve HTTP handler)"
    )
    approval_correlation_id = approved.correlation_id

    await drain_chain(harness.broker, offsets=offsets)

    sent = log_envelopes(harness.broker, Topic.OUTREACH_SENT)[0]
    assert sent.event_type == EventType.OUTREACH_SENT
    assert sent.event_version == EVENT_VERSION
    assert str(sent.user_id) == user["id"]
    # Correlation propagates correctly *from the approval point forward*.
    assert sent.correlation_id == approval_correlation_id
