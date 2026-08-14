"""Full-graph integration tests for the JobMatchingState workflow
(docs/architecture/langgraph-state.md#jobmatchingstate), against the
compiled graph from `workflows.langgraph.job_matching.graph.build_graph`.

Covers task scenarios A (single profile), B (multiple profiles), C
(cross-profession, no hard-coded profession assumption), D (poor match), E
(strong match), F (LLM failure), and I (multi-resume validation — the
concrete Mechanical/Software/Manufacturing scenario reported in the
implementation report).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

import matching.events as events_module
import workflows.langgraph.job_matching.nodes as nodes_module
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize
from infrastructure.kafka.topics import Topic
from matching.db import set_session_factory
from shared.types.enums import MatchRecommendation
from shared.types.ids import UserId
from tests.matching.conftest import (
    FakeLLMClient,
    FakeProfileServiceClient,
    make_normalized_job,
    make_resume_profile,
    make_user_preferences,
)
from workflows.langgraph.job_matching.graph import build_graph
from workflows.langgraph.job_matching.scoring import ProfileScoringOutput

pytestmark = pytest.mark.asyncio


def _initial_state(job, preferences) -> dict:
    return {
        "job": job,
        "profiles": [],
        "preferences": preferences,
        "profile_scores": [],
        "selected_profile": None,
        "selected_resume_id": None,
        "recommendation": None,
        "final_match": None,
        "errors": [],
    }


def _score(**overrides: object) -> ProfileScoringOutput:
    fields = {
        "role_relevance": 0.8,
        "skills_fit": 0.8,
        "experience_fit": 0.8,
        "domain_fit": 0.8,
        "seniority_fit": 0.8,
        "location_preference_fit": 0.7,
        "overall_score": 0.8,
        "matched_skills": [],
        "missing_skills": [],
        "reasoning": "scripted",
    }
    fields.update(overrides)
    return ProfileScoringOutput(**fields)


async def _run(job, preferences, *, profiles, llm_results, session_factory) -> dict:
    user_id = job.user_id
    set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("job-matching-service", client=InMemoryProducerClient(InMemoryBroker()))
    )
    nodes_module.set_profile_service_client(FakeProfileServiceClient({user_id: profiles}))
    nodes_module.set_llm_client(FakeLLMClient(llm_results))

    graph = build_graph()
    state = _initial_state(job, preferences)
    return await graph.ainvoke(state)


async def _insert_stand_in_jobs_row(session_factory, job) -> None:
    from sqlalchemy import Column, MetaData, String, Table, Uuid, insert

    table = Table(
        "jobs",
        MetaData(),
        Column("id", Uuid, primary_key=True),
        Column("company", String, nullable=False),
        Column("processing_status", String, nullable=False),
    )
    async with session_factory() as session:
        await session.execute(
            insert(table).values(id=job.job_id, company=job.company, processing_status="NORMALIZED")
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Scenario A — single profile
# ---------------------------------------------------------------------------


async def test_single_profile_produces_valid_match_result(session_factory) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    profile = make_resume_profile(user_id, title="Mechanical Design Engineer")
    await _insert_stand_in_jobs_row(session_factory, job)

    final_state = await _run(
        job,
        make_user_preferences(user_id),
        profiles=[profile],
        llm_results={"Mechanical Design Engineer": _score(overall_score=0.82)},
        session_factory=session_factory,
    )

    result = final_state["final_match"]
    assert result is not None
    assert result.selected_profile_id == profile.profile_id
    assert result.selected_resume_id == profile.resume_id
    assert result.match_score == pytest.approx(0.82)
    assert result.recommendation == MatchRecommendation.SHORTLIST


# ---------------------------------------------------------------------------
# Scenario B — multiple profiles, correct best selected
# ---------------------------------------------------------------------------


async def test_multiple_profiles_selects_correct_best_by_score(session_factory) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    ai_profile = make_resume_profile(user_id, title="AI Engineer Resume")
    java_profile = make_resume_profile(user_id, title="Java Backend Resume")
    fullstack_profile = make_resume_profile(user_id, title="Full-Stack Resume")
    await _insert_stand_in_jobs_row(session_factory, job)

    final_state = await _run(
        job,
        make_user_preferences(user_id),
        profiles=[ai_profile, java_profile, fullstack_profile],
        llm_results={
            "AI Engineer Resume": _score(overall_score=0.63),
            "Java Backend Resume": _score(overall_score=0.92),
            "Full-Stack Resume": _score(overall_score=0.74),
        },
        session_factory=session_factory,
    )

    result = final_state["final_match"]
    assert result.selected_profile_id == java_profile.profile_id
    assert result.match_score == pytest.approx(0.92)
    assert result.recommendation == MatchRecommendation.SHORTLIST


# ---------------------------------------------------------------------------
# Scenario C — cross-profession, no hard-coded profession assumption
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("job_title", "job_skills", "profile_titles_and_scores", "expected_best_title"),
    [
        (
            "Mechanical Design Engineer",
            ["CAD", "GD&T", "DFM"],
            {"Mechanical Design Profile": 0.9, "HR Business Partner Profile": 0.1},
            "Mechanical Design Profile",
        ),
        (
            "HR Business Partner",
            ["Talent Acquisition", "Employee Relations", "HRIS"],
            {"HR Business Partner Profile": 0.88, "Mechanical Design Profile": 0.05},
            "HR Business Partner Profile",
        ),
        (
            "Backend Software Engineer",
            ["Java", "Spring Boot", "Kafka"],
            {"Java Backend Profile": 0.91, "HR Business Partner Profile": 0.08},
            "Java Backend Profile",
        ),
    ],
)
async def test_cross_profession_matching_has_no_hard_coded_assumption(
    session_factory, job_title, job_skills, profile_titles_and_scores, expected_best_title
) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id, title=job_title, extracted_skills=job_skills)
    profiles = [
        make_resume_profile(user_id, title=title) for title in profile_titles_and_scores
    ]
    await _insert_stand_in_jobs_row(session_factory, job)

    final_state = await _run(
        job,
        make_user_preferences(user_id),
        profiles=profiles,
        llm_results={
            title: _score(overall_score=score)
            for title, score in profile_titles_and_scores.items()
        },
        session_factory=session_factory,
    )

    selected = final_state["selected_profile"]
    assert selected.title == expected_best_title


# ---------------------------------------------------------------------------
# Scenario D — poor match: jobs.matched only, not jobs.shortlisted
# ---------------------------------------------------------------------------


async def test_poor_match_publishes_matched_but_not_shortlisted(session_factory) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    profile = make_resume_profile(user_id, title="Mismatched Profile")
    await _insert_stand_in_jobs_row(session_factory, job)

    broker = InMemoryBroker()
    set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("job-matching-service", client=InMemoryProducerClient(broker))
    )
    nodes_module.set_profile_service_client(FakeProfileServiceClient({user_id: [profile]}))
    nodes_module.set_llm_client(
        FakeLLMClient({"Mismatched Profile": _score(overall_score=0.15)})
    )

    graph = build_graph()
    final_state = await graph.ainvoke(_initial_state(job, make_user_preferences(user_id)))

    assert final_state["final_match"].recommendation == MatchRecommendation.IGNORE
    assert len(broker.log(Topic.JOBS_MATCHED.value)) == 1
    assert len(broker.log(Topic.JOBS_SHORTLISTED.value)) == 0
    assert len(broker.log(Topic.CONTACTS_REQUESTED.value)) == 0


# ---------------------------------------------------------------------------
# Scenario E — strong match: both jobs.matched and jobs.shortlisted
# ---------------------------------------------------------------------------


async def test_strong_match_publishes_matched_and_shortlisted(session_factory) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    profile = make_resume_profile(user_id, title="Excellent Fit Profile")
    await _insert_stand_in_jobs_row(session_factory, job)

    broker = InMemoryBroker()
    set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("job-matching-service", client=InMemoryProducerClient(broker))
    )
    nodes_module.set_profile_service_client(FakeProfileServiceClient({user_id: [profile]}))
    nodes_module.set_llm_client(
        FakeLLMClient({"Excellent Fit Profile": _score(overall_score=0.95)})
    )

    graph = build_graph()
    final_state = await graph.ainvoke(_initial_state(job, make_user_preferences(user_id)))

    assert final_state["final_match"].recommendation == MatchRecommendation.SHORTLIST
    assert len(broker.log(Topic.JOBS_MATCHED.value)) == 1
    assert len(broker.log(Topic.JOBS_SHORTLISTED.value)) == 1
    contacts_requested_messages = broker.log(Topic.CONTACTS_REQUESTED.value)
    assert len(contacts_requested_messages) == 1
    published = deserialize(Topic.CONTACTS_REQUESTED, contacts_requested_messages[0].value())
    assert published.payload.job_id == job.job_id
    assert published.payload.company == job.company
    assert published.payload.title == job.title
    assert published.payload.location == job.location


# ---------------------------------------------------------------------------
# Scenario F — LLM failure: graceful termination, not a crash
# ---------------------------------------------------------------------------


async def test_partial_llm_failure_still_reaches_valid_terminal_state(session_factory) -> None:
    from infrastructure.llm import LLMFailureReason, LLMProviderError

    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    failing_profile = make_resume_profile(user_id, title="Failing Profile")
    healthy_profile = make_resume_profile(user_id, title="Healthy Profile")
    await _insert_stand_in_jobs_row(session_factory, job)

    error = LLMProviderError(
        LLMFailureReason.TIMEOUT, "timed out", provider="fake", model="fake-model"
    )
    final_state = await _run(
        job,
        make_user_preferences(user_id),
        profiles=[failing_profile, healthy_profile],
        llm_results={"Failing Profile": error, "Healthy Profile": _score(overall_score=0.81)},
        session_factory=session_factory,
    )

    assert final_state["final_match"] is not None
    assert final_state["final_match"].selected_profile_id == healthy_profile.profile_id
    assert any(e.error_code.value == "LLM_PROVIDER_ERROR" for e in final_state["errors"])


async def test_total_llm_failure_still_completes_with_ignore(session_factory) -> None:
    from infrastructure.llm import LLMFailureReason, LLMProviderError

    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    profiles = [make_resume_profile(user_id, title=f"Profile {i}") for i in range(2)]
    await _insert_stand_in_jobs_row(session_factory, job)

    error = LLMProviderError(
        LLMFailureReason.CONNECTION_ERROR, "unreachable", provider="fake", model="fake-model"
    )

    set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("job-matching-service", client=InMemoryProducerClient(InMemoryBroker()))
    )
    nodes_module.set_profile_service_client(FakeProfileServiceClient({user_id: profiles}))
    nodes_module.set_llm_client(FakeLLMClient({}, default=error))

    graph = build_graph()
    final_state = await graph.ainvoke(_initial_state(job, make_user_preferences(user_id)))

    # Reaches a valid terminal state rather than crashing/raising.
    assert final_state["final_match"] is not None
    assert final_state["final_match"].recommendation == MatchRecommendation.IGNORE
    assert len(final_state["errors"]) == len(profiles)


# ---------------------------------------------------------------------------
# Scenario I — Multi-Resume Validation (reported concrete scenario)
# ---------------------------------------------------------------------------


async def test_multi_resume_validation_mechanical_job_selects_mechanical_profile(
    session_factory,
) -> None:
    """The brief's own illustration: a Mechanical Design Engineer job
    scored against a Software Engineer resume, a Mechanical Design resume,
    and a Manufacturing resume. The Mechanical Design profile must score
    highest and be selected.
    """
    user_id = UserId(uuid4())
    job = make_normalized_job(
        user_id,
        title="Mechanical Design Engineer",
        company="Acme Robotics",
        description=(
            "Own mechanical subsystem design end to end: CAD modeling in "
            "SolidWorks, GD&T tolerance stack-ups, DFM/DFA reviews with "
            "manufacturing partners, and prototype validation testing for "
            "industrial robot arms."
        ),
        extracted_skills=["CAD", "SolidWorks", "GD&T", "DFM", "Tolerance Analysis"],
        experience_required="5+ years",
    )
    await _insert_stand_in_jobs_row(session_factory, job)

    software_profile = make_resume_profile(
        user_id,
        title="Software Engineer Resume",
        summary="Backend software engineer building distributed systems.",
        skills=["Python", "Kafka", "PostgreSQL", "Kubernetes"],
        experience_years=6.0,
        seniority="Senior",
        industries=["SaaS"],
        target_roles=["Backend Software Engineer"],
    )
    mechanical_profile = make_resume_profile(
        user_id,
        title="Mechanical Design Resume",
        summary="Mechanical design engineer specializing in CAD and DFM.",
        skills=["CAD", "SolidWorks", "GD&T", "DFM", "Tolerance Analysis"],
        experience_years=7.0,
        seniority="Senior",
        industries=["Robotics"],
        target_roles=["Mechanical Design Engineer"],
    )
    manufacturing_profile = make_resume_profile(
        user_id,
        title="Manufacturing Resume",
        summary="Manufacturing engineer focused on production line optimization.",
        skills=["Six Sigma", "Lean Manufacturing", "Production Planning"],
        experience_years=5.0,
        seniority="Senior",
        industries=["Manufacturing"],
        target_roles=["Manufacturing Engineer"],
    )

    # Scores mirror what a genuine semantic read would produce: high
    # role/skills/domain fit for the Mechanical Design resume, moderate
    # (shared "hands-on hardware" domain, no CAD/GD&T skill overlap) for
    # Manufacturing, low (no domain/skill overlap at all) for Software.
    final_state = await _run(
        job,
        make_user_preferences(user_id, target_roles=["Mechanical Design Engineer"]),
        profiles=[software_profile, mechanical_profile, manufacturing_profile],
        llm_results={
            "Software Engineer Resume": _score(
                overall_score=0.12,
                role_relevance=0.05,
                skills_fit=0.0,
                domain_fit=0.1,
                matched_skills=[],
                missing_skills=["CAD", "SolidWorks", "GD&T", "DFM"],
            ),
            "Mechanical Design Resume": _score(
                overall_score=0.94,
                role_relevance=0.95,
                skills_fit=0.95,
                domain_fit=0.95,
                matched_skills=["CAD", "SolidWorks", "GD&T", "DFM"],
                missing_skills=[],
            ),
            "Manufacturing Resume": _score(
                overall_score=0.41,
                role_relevance=0.35,
                skills_fit=0.2,
                domain_fit=0.55,
                matched_skills=[],
                missing_skills=["CAD", "SolidWorks", "GD&T"],
            ),
        },
        session_factory=session_factory,
    )

    result = final_state["final_match"]
    scores_by_title = {
        p.title: next(
            s.score
            for s in final_state["profile_scores"]
            if s.profile_id == p.profile_id
        )
        for p in [software_profile, mechanical_profile, manufacturing_profile]
    }

    assert scores_by_title["Software Engineer Resume"] == pytest.approx(0.12)
    assert scores_by_title["Mechanical Design Resume"] == pytest.approx(0.94)
    assert scores_by_title["Manufacturing Resume"] == pytest.approx(0.41)
    assert result.selected_profile_id == mechanical_profile.profile_id
    assert result.selected_resume_id == mechanical_profile.resume_id
    assert result.recommendation == MatchRecommendation.SHORTLIST
