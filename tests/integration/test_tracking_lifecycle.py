"""Tracking validation: the full event chain (both `jobs.discovered`
consumer groups included) drives `Application.status` to `OUTREACH_SENT`
via the exact path in state-machines.md, with correctly ordered
`ApplicationHistory` rows; duplicate delivery is a no-op; genuine
out-of-order arrival is tolerated per tracking/consumers.py's own
documented create-or-advance rule; and no source component calls Tracking's
API.

See docs/architecture/state-machines.md#opportunity-lifecycle and the task
brief's section 7.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from uuid import uuid4

import pytest

import contacts.api.routes as contacts_routes
import contacts.consumers as contacts_consumers
import jobs.discovery.api as jobs_discovery_api
import jobs.ingestion.api as jobs_ingestion_api
import matching.api.routes as matching_routes
import matching.consumers as matching_consumers
import outreach.api.routes as outreach_routes
import outreach.consumers as outreach_consumers
import profiles.api.routes as profiles_routes
import tracking.consumers as tracking_consumers
from infrastructure.kafka.serialization import build_envelope
from infrastructure.kafka.topics import Topic
from shared.types.enums import ApplicationStatus, ContactType
from shared.types.ids import UserId
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
from tests.matching.conftest import make_normalized_job
from tests.tracking.conftest import make_job_match_result
from workflows.langgraph.contact_discovery.discovery import (
    ContactClassificationBatch,
    ContactSearchPlan,
    HitClassification,
    RelevanceSignals,
)
from workflows.langgraph.job_matching.scoring import ProfileScoringOutput
from workflows.langgraph.outreach_generation.generation import OutreachDraftContent

JOB_URL = "https://boards.example.com/jobs/tracking-lifecycle-1"

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


async def _drive_to_outreach_generated(harness: IntegrationHarness) -> tuple[str, str, dict]:
    """Shared setup: drives the chain from jobs.discovered through
    outreach.generated. Returns (job_id, outreach_id, user).
    """
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
    await dispatch(Topic.OUTREACH_GENERATED, generated)

    return job_id, str(generated.payload.outreach_id), user


@pytest.mark.asyncio
async def test_lifecycle_reaches_outreach_sent_with_ordered_history(
    harness: IntegrationHarness,
) -> None:
    _job_id, outreach_id, user = await _drive_to_outreach_generated(harness)

    harness.approve_outreach(outreach_id)
    approved = log_envelopes(harness.broker, Topic.OUTREACH_APPROVED)[0]
    await dispatch(Topic.OUTREACH_APPROVED, approved)
    sent = log_envelopes(harness.broker, Topic.OUTREACH_SENT)[0]
    await dispatch(Topic.OUTREACH_SENT, sent)

    applications = harness.client.get(
        "/applications", headers=harness.auth_headers(user)
    ).json()
    assert len(applications) == 1
    application = applications[0]
    assert application["status"] == ApplicationStatus.OUTREACH_SENT.value

    history = harness.client.get(f"/applications/{application['id']}/history").json()
    pairs = [(entry["from_status"], entry["to_status"]) for entry in history]
    assert pairs == [
        (None, "DISCOVERED"),
        ("DISCOVERED", "MATCHED"),
        ("MATCHED", "SHORTLISTED"),
        ("SHORTLISTED", "CONTACT_SEARCH"),
        ("CONTACT_SEARCH", "CONTACT_FOUND"),
        ("CONTACT_FOUND", "OUTREACH_GENERATED"),
        ("OUTREACH_GENERATED", "OUTREACH_APPROVED"),
        ("OUTREACH_APPROVED", "OUTREACH_SENT"),
    ]
    changed_at = [entry["changed_at"] for entry in history]
    assert changed_at == sorted(changed_at)


@pytest.mark.asyncio
async def test_duplicate_delivery_produces_no_duplicate_history_row(
    harness: IntegrationHarness,
) -> None:
    _job_id, _outreach_id, user = await _drive_to_outreach_generated(harness)

    generated_envelope = log_envelopes(harness.broker, Topic.OUTREACH_GENERATED)[0]
    # Redeliver the identical envelope a second time directly.
    await dispatch(Topic.OUTREACH_GENERATED, generated_envelope)

    applications = harness.client.get(
        "/applications", headers=harness.auth_headers(user)
    ).json()
    application_id = applications[0]["id"]
    history = harness.client.get(f"/applications/{application_id}/history").json()
    generated_entries = [h for h in history if h["to_status"] == "OUTREACH_GENERATED"]
    assert len(generated_entries) == 1


@pytest.mark.asyncio
async def test_out_of_order_jobs_matched_before_jobs_discovered_creates_application(
    harness: IntegrationHarness,
) -> None:
    """Build and deliver a jobs.matched-shaped envelope for a job_id before
    ever delivering its jobs.discovered — tracking.consumers's
    create-or-advance behavior must create the Application row on whichever
    event arrives first (kafka-topics.md's "no cross-topic ordering
    guarantee").
    """
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    match_result = make_job_match_result(job.job_id, user_id)

    matched_envelope = build_envelope(
        Topic.JOBS_MATCHED, match_result, producer="job-matching-service"
    )
    await tracking_consumers._handle_job_matched_async(matched_envelope)

    from infrastructure.auth import create_access_token

    applications = harness.client.get(
        "/applications", headers={"Authorization": f"Bearer {create_access_token(user_id)}"}
    ).json()
    assert len(applications) == 1
    application = applications[0]
    assert application["status"] == ApplicationStatus.MATCHED.value
    # No jobs.discovered was ever seen, so company/title started as "" —
    # see tracking/consumers.py's "Denormalized company/title backfill" note.
    assert application["company"] == ""
    assert application["selected_resume_id"] == str(match_result.selected_resume_id)

    # jobs.discovered arrives afterward and backfills company/title without
    # regressing status (DISCOVERED has the lowest rank).
    discovered_envelope = build_envelope(
        Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service"
    )
    await tracking_consumers._handle_job_discovered_async(discovered_envelope)

    application = harness.client.get(f"/applications/{application['id']}").json()
    assert application["status"] == ApplicationStatus.MATCHED.value
    assert application["company"] == job.company

    history = harness.client.get(f"/applications/{application['id']}/history").json()
    # Only one real transition happened (-> MATCHED); the later
    # jobs.discovered backfill is not itself a status transition.
    assert [h["to_status"] for h in history] == [ApplicationStatus.MATCHED.value]


def test_no_source_component_calls_tracking_api() -> None:
    """Grep-check mirroring tests/contacts/test_no_circular_dependency.py's
    technique: every producing component's own source must never import
    `tracking`'s package or reference its API path — Tracking pulls its
    picture of the world entirely from events (service-boundaries.md
    #tracking-service, dependency-graph.md's "no circular dependencies").
    """
    modules = (
        matching_routes,
        matching_consumers,
        contacts_routes,
        contacts_consumers,
        outreach_routes,
        outreach_consumers,
        jobs_ingestion_api,
        jobs_discovery_api,
        profiles_routes,
    )
    for module in modules:
        source = inspect.getsource(module)
        assert "import tracking" not in source, module.__name__
        assert "from tracking" not in source, module.__name__
        assert "/applications" not in source, module.__name__


def test_no_source_file_under_src_imports_tracking_package() -> None:
    """Broader sweep: no file outside `tracking/` itself imports the
    `tracking` package.
    """
    src_root = Path(__file__).resolve().parents[2] / "src"
    offenders = []
    for path in src_root.rglob("*.py"):
        top_level = path.relative_to(src_root).parts[0]
        # api/main.py is the one approved app-assembly composition root that
        # mounts every component's router, including tracking's — see
        # repository-structure.md ("this module only wires them together").
        # That is not a business component calling Tracking's API.
        if top_level in ("tracking", "api"):
            continue
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "import tracking" in text or "from tracking" in text:
            offenders.append(str(path))
    assert offenders == []
