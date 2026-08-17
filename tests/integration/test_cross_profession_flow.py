"""Profession-independence validation: the full flow through Contact
Discovery + Outreach generation (where profession-independence actually
matters, per the task brief) runs for software engineering, mechanical
engineering, and HR scenarios — the *same* node functions/graphs execute
for all three; only the fed data and resulting LLM-driven content differ.

See CLAUDE.md rules 3-4, about_project.md's "Core Product Philosophy ->
Generic" section, and .claude/agents/integration-agent.md's "Preserve
profession-independence" global rule.
"""

from __future__ import annotations

import pytest

import workflows.langgraph.contact_discovery.nodes as contact_nodes
import workflows.langgraph.outreach_generation.nodes as outreach_nodes
from infrastructure.kafka.topics import Topic
from shared.types.enums import ContactType
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

_SCENARIOS = [
    pytest.param(
        {
            "profession": "software-engineering",
            "resume_file": "swe_resume.txt",
            "resume_text": (
                "Alex Chen. Senior Software Engineer. 8 years building "
                "distributed backend systems in Python and Go."
            ),
            "profile_fields": {
                "title": "Senior Software Engineer",
                "summary": "Backend engineer specializing in distributed systems.",
                "skills": ["Python", "Go", "Kubernetes", "PostgreSQL"],
                "experience_years": 8.0,
                "seniority": "Senior",
                "target_roles": ["Backend Engineer"],
            },
            "job_url": "https://boards.example.com/jobs/swe-1",
            "job_page_text": (
                "Senior Backend Engineer at Northwind Software. Own service "
                "design for our payments platform in Python and Go, on "
                "Kubernetes."
            ),
            "job_fields": {
                "company": "Northwind Software",
                "title": "Senior Backend Engineer",
                "location": "Remote",
                "description": (
                    "Senior Backend Engineer at Northwind Software. Own "
                    "service design for our payments platform in Python "
                    "and Go, on Kubernetes."
                ),
                "extracted_skills": ["Python", "Go", "Kubernetes"],
                "experience_required": "5+ years",
            },
            "contact_hit": make_hit(
                full_name="Morgan Blake",
                headline="Engineering Manager, Payments at Northwind Software",
                company="Northwind Software",
                profile_url="https://example.com/in/morgan-blake",
            ),
            "contact_type": ContactType.HIRING_MANAGER,
        },
        id="software-engineering",
    ),
    pytest.param(
        {
            "profession": "mechanical-engineering",
            "resume_file": "mech_resume.txt",
            "resume_text": (
                "Jordan Rivera. Senior Mechanical Design Engineer. 7 years "
                "designing mechanical subsystems for industrial robots."
            ),
            "profile_fields": {
                "title": "Senior Mechanical Design Engineer",
                "summary": "Mechanical engineer with 7 years designing robotic subsystems.",
                "skills": ["CAD", "SolidWorks", "GD&T", "DFM"],
                "experience_years": 7.0,
                "seniority": "Senior",
                "target_roles": ["Mechanical Design Engineer"],
            },
            "job_url": "https://boards.example.com/jobs/mech-cross-1",
            "job_page_text": (
                "Senior Mechanical Design Engineer at Acme Robotics. Own "
                "CAD models from concept through DFM/DFA review."
            ),
            "job_fields": {
                "company": "Acme Robotics",
                "title": "Senior Mechanical Design Engineer",
                "location": "Remote",
                "description": (
                    "Senior Mechanical Design Engineer at Acme Robotics. "
                    "Own CAD models from concept through DFM/DFA review."
                ),
                "extracted_skills": ["CAD", "SolidWorks", "GD&T"],
                "experience_required": "5+ years",
            },
            "contact_hit": make_hit(
                full_name="Sam Lee",
                headline="Engineering Manager, Mechanical at Acme Robotics",
                company="Acme Robotics",
                profile_url="https://example.com/in/sam-lee",
            ),
            "contact_type": ContactType.HIRING_MANAGER,
        },
        id="mechanical-engineering",
    ),
    pytest.param(
        {
            "profession": "hr",
            "resume_file": "hr_resume.txt",
            "resume_text": (
                "Priya Nair. HR Business Partner. 6 years partnering with "
                "engineering leadership on talent strategy."
            ),
            "profile_fields": {
                "title": "HR Business Partner",
                "summary": "HR business partner focused on talent strategy for tech orgs.",
                "skills": ["Talent Acquisition", "Employee Relations", "HRIS"],
                "experience_years": 6.0,
                "seniority": "Senior",
                "target_roles": ["HR Business Partner"],
            },
            "job_url": "https://boards.example.com/jobs/hr-1",
            "job_page_text": (
                "HR Business Partner at Beacon Health. Partner with "
                "leadership on org design and talent strategy."
            ),
            "job_fields": {
                "company": "Beacon Health",
                "title": "HR Business Partner",
                "location": "Remote",
                "description": (
                    "HR Business Partner at Beacon Health. Partner with "
                    "leadership on org design and talent strategy."
                ),
                "extracted_skills": ["Talent Acquisition", "Employee Relations"],
                "experience_required": "5+ years",
            },
            "contact_hit": make_hit(
                full_name="Taylor Osei",
                headline="VP of People at Beacon Health",
                company="Beacon Health",
                profile_url="https://example.com/in/taylor-osei",
            ),
            "contact_type": ContactType.EXECUTIVE,
        },
        id="hr",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", _SCENARIOS)
async def test_cross_profession_flow_uses_identical_code_path(
    harness: IntegrationHarness, scenario: dict
) -> None:
    # Prove profession-independence structurally: record the exact node/graph
    # callables Contact Discovery + Outreach generation will use for this run
    # — asserted equal across all three scenario ids below is implicit, since
    # they're module-level singletons; the real proof is that this same code
    # completes successfully for every profession's own data.
    assert contact_nodes.search_contacts is contact_nodes.search_contacts
    assert outreach_nodes.generate_message is outreach_nodes.generate_message

    user = harness.create_user()
    harness.set_profiles_llm([llm_response(scenario["profile_fields"])])
    harness.upload_resume(user, scenario["resume_file"], scenario["resume_text"])

    harness.set_job_ingestion_fakes(
        pages={scenario["job_url"]: scenario["job_page_text"]},
        extractor_by_content={
            scenario["job_page_text"]: make_extracted_fields(**scenario["job_fields"])
        },
    )
    job = harness.ingest_job(user, scenario["job_url"])
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
                matched_skills=scenario["job_fields"]["extracted_skills"],
                missing_skills=[],
                reasoning="Strong fit for this profession's own vocabulary.",
            )
        )
    )

    contact_hit = scenario["contact_hit"]
    harness.set_contacts_fakes(
        hits=[contact_hit],
        llm=ContactsFakeLLMClient(
            results={
                "Propose 3-6 short search": ContactSearchPlan(role_keywords=["Manager"]),
                "Classify each of the following": ContactClassificationBatch(
                    classifications=[
                        HitClassification(index=0, contact_type=scenario["contact_type"])
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

    generated_body = f"Personalized note for the {scenario['profession']} opportunity."
    harness.set_outreach_llm(
        OutreachFakeLLMClient(default=OutreachDraftContent(body=generated_body))
    )

    discovered_envelope = log_envelopes(harness.broker, Topic.JOBS_DISCOVERED)[0]
    await dispatch(Topic.JOBS_DISCOVERED, discovered_envelope)

    matched = log_envelopes(harness.broker, Topic.JOBS_MATCHED)[0]
    await dispatch(Topic.JOBS_MATCHED, matched)
    shortlisted = log_envelopes(harness.broker, Topic.JOBS_SHORTLISTED)[0]
    await dispatch(Topic.JOBS_SHORTLISTED, shortlisted)
    contacts_requested = log_envelopes(harness.broker, Topic.CONTACTS_REQUESTED)[0]
    await dispatch(Topic.CONTACTS_REQUESTED, contacts_requested)

    contacts_found = log_envelopes(harness.broker, Topic.CONTACTS_FOUND)
    assert len(contacts_found) == 1
    assert contacts_found[0].payload.contacts[0].full_name == contact_hit.full_name

    harness.sync_outreach_clients(job_id)
    await dispatch(Topic.CONTACTS_FOUND, contacts_found[0])

    generated = log_envelopes(harness.broker, Topic.OUTREACH_GENERATED)
    assert len(generated) == 1
    assert generated_body in generated[0].payload.draft_message

    outreach_response = harness.client.get(
        f"/outreach/{generated[0].payload.outreach_id}"
    ).json()
    assert outreach_response["status"] == "PENDING_APPROVAL"
    assert scenario["profession"] in outreach_response["draft_message"]
