"""Unit tests for the five JobMatchingState node functions
(docs/architecture/langgraph-state.md#jobmatchingstate).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

import workflows.langgraph.job_matching.nodes as nodes_module
from infrastructure.llm import LLMFailureReason, LLMProviderError
from matching.errors import MatchingError
from matching.repository import JobMatchRepository
from shared.errors.codes import ErrorCode
from shared.types.enums import MatchRecommendation, ProfileStatus
from shared.types.ids import UserId
from tests.matching.conftest import (
    FakeLLMClient,
    FakeProfileServiceClient,
    make_normalized_job,
    make_resume_profile,
    make_user_preferences,
)
from workflows.langgraph.job_matching.nodes import (
    BORDERLINE_THRESHOLD,
    SHORTLIST_THRESHOLD,
    compute_recommendation,
    load_profiles,
    persist_and_publish,
    score_profile,
    select_best_profile,
)
from workflows.langgraph.job_matching.scoring import ProfileScoringOutput

pytestmark = pytest.mark.asyncio


def _initial_state(job, preferences, profiles=None) -> dict:
    return {
        "job": job,
        "profiles": profiles or [],
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
        "matched_skills": ["CAD"],
        "missing_skills": [],
        "reasoning": "Good fit.",
    }
    fields.update(overrides)
    return ProfileScoringOutput(**fields)


# ---------------------------------------------------------------------------
# load_profiles
# ---------------------------------------------------------------------------


async def test_load_profiles_filters_to_active_only() -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    active = make_resume_profile(user_id, title="Active One", status=ProfileStatus.ACTIVE)
    archived = make_resume_profile(user_id, title="Archived One", status=ProfileStatus.ARCHIVED)
    nodes_module.set_profile_service_client(
        FakeProfileServiceClient({user_id: [active, archived]})
    )

    state = _initial_state(job, make_user_preferences(user_id))
    result = await load_profiles(state)

    assert [p.profile_id for p in result["profiles"]] == [active.profile_id]


async def test_load_profiles_short_circuits_when_no_active_profiles() -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    nodes_module.set_profile_service_client(FakeProfileServiceClient({user_id: []}))

    state = _initial_state(job, make_user_preferences(user_id))
    result = await load_profiles(state)

    assert result["profiles"] == []
    assert result["recommendation"] == MatchRecommendation.IGNORE
    assert len(result["errors"]) == 1
    assert result["errors"][0].error_code == ErrorCode.NO_PROFILES_AVAILABLE
    assert result["errors"][0].node == "load_profiles"


# ---------------------------------------------------------------------------
# score_profile
# ---------------------------------------------------------------------------


async def test_score_profile_discriminates_between_profiles() -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    strong = make_resume_profile(user_id, title="Strong Fit Profile")
    weak = make_resume_profile(user_id, title="Weak Fit Profile")
    nodes_module.set_llm_client(
        FakeLLMClient(
            {
                "Strong Fit Profile": _score(overall_score=0.92),
                "Weak Fit Profile": _score(overall_score=0.2),
            }
        )
    )

    state = _initial_state(job, make_user_preferences(user_id), [strong, weak])
    result = await score_profile(state)

    by_id = {s.profile_id: s.score for s in result["profile_scores"]}
    assert by_id[strong.profile_id] == pytest.approx(0.92)
    assert by_id[weak.profile_id] == pytest.approx(0.2)
    assert result["errors"] == []


async def test_score_profile_llm_failure_records_zero_and_continues() -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    failing = make_resume_profile(user_id, title="Failing Profile")
    succeeding = make_resume_profile(user_id, title="Succeeding Profile")
    error = LLMProviderError(
        LLMFailureReason.TIMEOUT, "timed out", provider="fake", model="fake-model"
    )
    nodes_module.set_llm_client(
        FakeLLMClient(
            {
                "Failing Profile": error,
                "Succeeding Profile": _score(overall_score=0.7),
            }
        )
    )

    state = _initial_state(job, make_user_preferences(user_id), [failing, succeeding])
    result = await score_profile(state)

    by_id = {s.profile_id: s.score for s in result["profile_scores"]}
    assert by_id[failing.profile_id] == 0.0
    assert by_id[succeeding.profile_id] == pytest.approx(0.7)
    assert len(result["errors"]) == 1
    assert result["errors"][0].error_code == ErrorCode.LLM_PROVIDER_ERROR
    assert result["errors"][0].node == "score_profile"


async def test_score_profile_all_fail_records_zero_for_every_profile() -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    profiles = [make_resume_profile(user_id, title=f"Profile {i}") for i in range(3)]
    error = LLMProviderError(
        LLMFailureReason.CONNECTION_ERROR, "unreachable", provider="fake", model="fake-model"
    )
    nodes_module.set_llm_client(FakeLLMClient({}, default=error))

    state = _initial_state(job, make_user_preferences(user_id), profiles)
    result = await score_profile(state)

    assert all(s.score == 0.0 for s in result["profile_scores"])
    assert len(result["errors"]) == 3


# ---------------------------------------------------------------------------
# select_best_profile
# ---------------------------------------------------------------------------


async def test_select_best_profile_picks_highest_score() -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    low = make_resume_profile(user_id, title="Low")
    high = make_resume_profile(user_id, title="High")
    state = _initial_state(job, make_user_preferences(user_id), [low, high])
    state["profile_scores"] = [
        _match_score(low, 0.3),
        _match_score(high, 0.95),
    ]

    result = await select_best_profile(state)

    assert result["selected_profile"].profile_id == high.profile_id
    assert result["selected_resume_id"] == high.resume_id


async def test_select_best_profile_breaks_ties_by_first_occurrence() -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    first = make_resume_profile(user_id, title="First")
    second = make_resume_profile(user_id, title="Second")
    state = _initial_state(job, make_user_preferences(user_id), [first, second])
    state["profile_scores"] = [_match_score(first, 0.6), _match_score(second, 0.6)]

    result = await select_best_profile(state)

    assert result["selected_profile"].profile_id == first.profile_id


def _match_score(profile, score: float):
    from shared.types.dto import ProfileMatchScore

    return ProfileMatchScore(profile_id=profile.profile_id, resume_id=profile.resume_id, score=score)


# ---------------------------------------------------------------------------
# compute_recommendation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (SHORTLIST_THRESHOLD, MatchRecommendation.SHORTLIST),
        (0.99, MatchRecommendation.SHORTLIST),
        (BORDERLINE_THRESHOLD, MatchRecommendation.BORDERLINE),
        (SHORTLIST_THRESHOLD - 0.01, MatchRecommendation.BORDERLINE),
        (0.0, MatchRecommendation.IGNORE),
        (BORDERLINE_THRESHOLD - 0.01, MatchRecommendation.IGNORE),
    ],
)
async def test_compute_recommendation_threshold_bands(score: float, expected) -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    profile = make_resume_profile(user_id)
    state = _initial_state(job, make_user_preferences(user_id), [profile])
    state["profile_scores"] = [_match_score(profile, score)]

    result = await compute_recommendation(state)

    assert result["recommendation"] == expected


# ---------------------------------------------------------------------------
# persist_and_publish
# ---------------------------------------------------------------------------


async def test_persist_and_publish_no_profiles_marks_job_failed(session_factory) -> None:
    from sqlalchemy import Column, MetaData, String, Table, Uuid, insert, select

    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)

    jobs_table = Table(
        "jobs",
        MetaData(),
        Column("id", Uuid, primary_key=True),
        Column("company", String, nullable=False),
        Column("processing_status", String, nullable=False),
    )
    async with session_factory() as session:
        await session.execute(
            insert(jobs_table).values(
                id=job.job_id, company=job.company, processing_status="NORMALIZED"
            )
        )
        await session.commit()

    from matching.db import set_session_factory

    set_session_factory(session_factory)

    state = _initial_state(job, make_user_preferences(user_id), [])
    result = await persist_and_publish(state)

    assert result["final_match"] is None

    async with session_factory() as session:
        stored = await JobMatchRepository(session).get_latest_for_job(job.job_id)
        assert stored is None
        row = (await session.execute(select(jobs_table).where(jobs_table.c.id == job.job_id))).one()
        assert row.processing_status == "FAILED"


async def test_persist_and_publish_raises_matching_error_when_selection_missing() -> None:
    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    profile = make_resume_profile(user_id)
    state = _initial_state(job, make_user_preferences(user_id), [profile])
    # selected_profile/selected_resume_id/recommendation intentionally left None

    with pytest.raises(MatchingError):
        await persist_and_publish(state)
