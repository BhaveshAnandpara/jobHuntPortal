"""Multi-resume validation: one user, three materially different resumes
(Software Engineer, Mechanical Design Engineer, HR Professional), one job
that clearly fits only the Mechanical Engineer profile.

Verifies: all three profiles are evaluated during matching, the correct
profile/resume is selected, and the same `selected_resume_id` propagates
through `JobMatchResult` (`jobs.matched`/`jobs.shortlisted`) and into
Tracking's `Application` record.

See .claude/agents/integration-agent.md's Required Validation items 4-5 and
the task brief's section 2.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from infrastructure.kafka.topics import Topic
from matching.repository import JobMatchRepository
from shared.types.enums import ApplicationStatus, MatchRecommendation
from tests.integration.conftest import (
    FakeUserPreferencesClient,
    IntegrationHarness,
    MatchingFakeLLMClient,
    dispatch,
    llm_response,
    log_envelopes,
)
from tests.jobs.conftest import make_extracted_fields
from workflows.langgraph.job_matching.scoring import ProfileScoringOutput

JOB_URL = "https://boards.example.com/jobs/mech-multi-1"

_JOB_PAGE_TEXT = (
    "Senior Mechanical Design Engineer at Acme Robotics. Own CAD models "
    "from concept through DFM/DFA review, run tolerance stack-ups, and "
    "validate designs with prototype testing. Requires SolidWorks, GD&T, "
    "DFM, tolerance analysis."
)

_SWE_RESUME_TEXT = (
    "Alex Chen. Senior Software Engineer. 8 years building distributed "
    "backend systems in Python and Go. Skills: Python, Go, Kubernetes, "
    "PostgreSQL, distributed systems, REST APIs, microservices."
)
_SWE_FIELDS = {
    "title": "Senior Software Engineer",
    "summary": "Backend engineer specializing in distributed systems.",
    "skills": ["Python", "Go", "Kubernetes", "PostgreSQL", "REST APIs"],
    "experience_years": 8.0,
    "seniority": "Senior",
    "education": [],
    "certifications": [],
    "projects": ["Payments microservice migration"],
    "industries": ["Software"],
    "target_roles": ["Backend Engineer", "Software Engineer"],
}

_MECH_RESUME_TEXT = (
    "Jordan Rivera. Senior Mechanical Design Engineer. 7 years designing "
    "mechanical subsystems for industrial robots. Skills: CAD, SolidWorks, "
    "GD&T, DFM, tolerance stack-up analysis. Led design reviews and "
    "prototype validation for three product launches."
)
_MECH_FIELDS = {
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

_HR_RESUME_TEXT = (
    "Priya Nair. HR Business Partner. 6 years partnering with engineering "
    "leadership on talent strategy, employee relations, and organizational "
    "design. Skills: talent acquisition, employee relations, HRIS, "
    "compensation planning, org design."
)
_HR_FIELDS = {
    "title": "HR Business Partner",
    "summary": "HR business partner focused on talent strategy for tech orgs.",
    "skills": ["Talent Acquisition", "Employee Relations", "HRIS", "Compensation"],
    "experience_years": 6.0,
    "seniority": "Senior",
    "education": [],
    "certifications": ["SHRM-CP"],
    "projects": [],
    "industries": ["Human Resources"],
    "target_roles": ["HR Business Partner", "HR Manager"],
}


@pytest.mark.asyncio
async def test_multi_resume_flow_selects_the_fitting_profile(
    harness: IntegrationHarness,
) -> None:
    user = harness.create_user()

    # Upload three resumes in order; the profiles LLM script is consumed
    # FIFO in upload order (see tests/profiles/conftest.py:ScriptedLLMProvider).
    harness.set_profiles_llm(
        [
            llm_response(_SWE_FIELDS),
            llm_response(_MECH_FIELDS),
            llm_response(_HR_FIELDS),
        ]
    )
    harness.upload_resume(user, "swe_resume.txt", _SWE_RESUME_TEXT)
    mech_resume = harness.upload_resume(user, "mech_resume.txt", _MECH_RESUME_TEXT)
    harness.upload_resume(user, "hr_resume.txt", _HR_RESUME_TEXT)

    profiles = harness.list_profiles(user)
    assert len(profiles) == 3
    titles = {p["title"] for p in profiles}
    assert titles == {
        "Senior Software Engineer",
        "Senior Mechanical Design Engineer",
        "HR Business Partner",
    }

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

    # Scripted results keyed by each profile's unique "CANDIDATE PROFILE\nTitle: ..."
    # line (see workflows/langgraph/job_matching/scoring.py:build_scoring_prompt) —
    # the winner emerges from this data, not from test business logic.
    matching_llm = MatchingFakeLLMClient(
        results={
            "CANDIDATE PROFILE\nTitle: Senior Mechanical Design Engineer": ProfileScoringOutput(
                role_relevance=0.95,
                skills_fit=0.95,
                experience_fit=0.9,
                domain_fit=0.9,
                seniority_fit=0.9,
                location_preference_fit=0.5,
                overall_score=0.93,
                matched_skills=["CAD", "SolidWorks", "GD&T", "DFM"],
                missing_skills=[],
                reasoning="Excellent mechanical design fit.",
            ),
            "CANDIDATE PROFILE\nTitle: Senior Software Engineer": ProfileScoringOutput(
                role_relevance=0.1,
                skills_fit=0.05,
                experience_fit=0.3,
                domain_fit=0.1,
                seniority_fit=0.3,
                location_preference_fit=0.5,
                overall_score=0.15,
                matched_skills=[],
                missing_skills=["CAD", "SolidWorks", "GD&T"],
                reasoning="No mechanical engineering skills.",
            ),
            "CANDIDATE PROFILE\nTitle: HR Business Partner": ProfileScoringOutput(
                role_relevance=0.05,
                skills_fit=0.02,
                experience_fit=0.2,
                domain_fit=0.05,
                seniority_fit=0.3,
                location_preference_fit=0.5,
                overall_score=0.08,
                matched_skills=[],
                missing_skills=["CAD", "SolidWorks", "GD&T"],
                reasoning="Not a mechanical engineering profile.",
            ),
        }
    )
    harness.set_matching_llm(matching_llm)

    discovered_envelope = log_envelopes(harness.broker, Topic.JOBS_DISCOVERED)[0]
    await dispatch(Topic.JOBS_DISCOVERED, discovered_envelope)

    # All three profiles were evaluated: three scoring prompts sent.
    assert len(matching_llm.prompts) == 3

    async with harness.session_factory() as session:
        job_match, published_at = await JobMatchRepository(
            session
        ).get_latest_for_job_with_published_at(UUID(job_id))
    assert job_match is not None
    assert published_at is not None
    assert len(job_match.profile_scores) == 3
    assert str(job_match.selected_resume_id) == mech_resume["id"]
    assert job_match.recommendation == MatchRecommendation.SHORTLIST
    assert job_match.match_score == pytest.approx(0.93)

    matched = log_envelopes(harness.broker, Topic.JOBS_MATCHED)
    assert len(matched) == 1
    assert str(matched[0].payload.selected_resume_id) == mech_resume["id"]
    await dispatch(Topic.JOBS_MATCHED, matched[0])

    shortlisted = log_envelopes(harness.broker, Topic.JOBS_SHORTLISTED)
    assert len(shortlisted) == 1
    assert str(shortlisted[0].payload.selected_resume_id) == mech_resume["id"]
    await dispatch(Topic.JOBS_SHORTLISTED, shortlisted[0])

    contacts_requested = log_envelopes(harness.broker, Topic.CONTACTS_REQUESTED)
    assert len(contacts_requested) == 1

    applications = harness.client.get(
        "/applications", headers=harness.auth_headers(user)
    ).json()
    assert len(applications) == 1
    assert applications[0]["selected_resume_id"] == mech_resume["id"]
    # Tracking's jobs.shortlisted handler advances SHORTLISTED -> CONTACT_SEARCH
    # in the same handler invocation (see tracking/consumers.py's module
    # docstring) — CONTACT_SEARCH is the settled status after this dispatch.
    assert applications[0]["status"] == ApplicationStatus.CONTACT_SEARCH.value
