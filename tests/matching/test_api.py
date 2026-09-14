"""API tests for `GET /jobs/{job_id}/matches` (FastAPI TestClient). The
repository dependency is overridden with an in-memory fake — no live
database required. Mirrors `tests/jobs/discovery/test_api.py`'s pattern.

`GET /jobs`/`GET /jobs/{job_id}` are deliberately not tested here — they
are not implemented (see `matching/api/routes.py`'s module docstring for
the documented architecture gap).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from matching.api.dependencies import get_job_match_repository
from matching.api.routes import router
from shared.types.domain.job_match import JobMatch
from shared.types.dto import ProfileMatchScore
from shared.types.enums import MatchRecommendation
from shared.types.ids import JobId, JobMatchId, ProfileId, ResumeId, UserId


class FakeJobMatchRepository:
    def __init__(self, matches: dict[JobId, JobMatch] | None = None) -> None:
        self._matches = matches or {}

    async def get_latest_for_job(self, job_id: JobId) -> JobMatch | None:
        return self._matches.get(job_id)


def _client(repository: FakeJobMatchRepository | None = None) -> tuple[TestClient, FakeJobMatchRepository]:
    app = FastAPI()
    app.include_router(router)
    repository = repository if repository is not None else FakeJobMatchRepository()
    app.dependency_overrides[get_job_match_repository] = lambda: repository
    return TestClient(app), repository


def _job_match(job_id: JobId) -> JobMatch:
    profile_id = ProfileId(uuid4())
    resume_id = ResumeId(uuid4())
    return JobMatch(
        id=JobMatchId(uuid4()),
        job_id=job_id,
        user_id=UserId(uuid4()),
        selected_profile_id=profile_id,
        selected_resume_id=resume_id,
        match_score=0.91,
        matched_skills=["CAD"],
        missing_skills=["Six Sigma"],
        recommendation=MatchRecommendation.SHORTLIST,
        profile_scores=[
            ProfileMatchScore(profile_id=profile_id, resume_id=resume_id, score=0.91)
        ],
        matched_at=datetime.now(UTC),
    )


def test_get_job_matches_returns_200_with_match_body() -> None:
    job_id = JobId(uuid4())
    job_match = _job_match(job_id)
    client, _ = _client(FakeJobMatchRepository({job_id: job_match}))

    response = client.get(f"/jobs/{job_id}/matches")

    assert response.status_code == 200
    body = response.json()
    assert body["job_match_id"] == str(job_match.id)
    assert body["selected_profile_id"] == str(job_match.selected_profile_id)
    assert body["selected_resume_id"] == str(job_match.selected_resume_id)
    assert body["match_score"] == 0.91
    assert body["recommendation"] == "SHORTLIST"


def test_get_job_matches_exposes_profile_scores() -> None:
    """Step 10.5: JobMatchResponse must expose profile_scores (the score
    against every evaluated profile, not just the winner) — previously
    flagged as a frontend-blocking gap (docs/frontend/routes.md) since the
    response didn't project the already-persisted JobMatch.profile_scores.
    """
    job_id = JobId(uuid4())
    job_match = _job_match(job_id)
    client, _ = _client(FakeJobMatchRepository({job_id: job_match}))

    response = client.get(f"/jobs/{job_id}/matches")

    assert response.status_code == 200
    body = response.json()
    assert len(body["profile_scores"]) == 1
    scored = body["profile_scores"][0]
    assert scored["profile_id"] == str(job_match.selected_profile_id)
    assert scored["resume_id"] == str(job_match.selected_resume_id)
    assert scored["score"] == 0.91


# ---------------------------------------------------------------------------
# The selected profile's explanation fields, projected onto the top level
# ---------------------------------------------------------------------------


def _job_match_with_scores(job_id: JobId, *, selected: ProfileMatchScore, others: list[ProfileMatchScore]) -> JobMatch:
    return JobMatch(
        id=JobMatchId(uuid4()),
        job_id=job_id,
        user_id=UserId(uuid4()),
        selected_profile_id=selected.profile_id,
        selected_resume_id=selected.resume_id,
        match_score=selected.score,
        matched_skills=selected.matched_skills,
        missing_skills=selected.missing_skills,
        recommendation=MatchRecommendation.SHORTLIST,
        profile_scores=[*others, selected],
        matched_at=datetime.now(UTC),
    )


def test_explanation_fields_come_from_the_selected_profiles_score() -> None:
    """`reasoning`/`hard_blocker`/`insufficient_detail` are projected from
    the `profile_scores` entry whose `profile_id == selected_profile_id`, so
    the user reads the explanation for the resume that actually won — not
    for whichever profile happens to sit first in the array.

    The losing profile below is deliberately given a blocker of its own; a
    naive "take the first entry" projection would surface it.
    """
    job_id = JobId(uuid4())
    selected = ProfileMatchScore(
        profile_id=ProfileId(uuid4()),
        resume_id=ResumeId(uuid4()),
        score=0.91,
        reasoning="The winning resume's explanation.",
        hard_blocker=None,
        insufficient_detail=False,
    )
    loser = ProfileMatchScore(
        profile_id=ProfileId(uuid4()),
        resume_id=ResumeId(uuid4()),
        score=0.32,
        reasoning="The losing resume's explanation.",
        hard_blocker="A blocker that applies only to the losing resume.",
        insufficient_detail=True,
    )
    client, _ = _client(
        FakeJobMatchRepository(
            {job_id: _job_match_with_scores(job_id, selected=selected, others=[loser])}
        )
    )

    body = client.get(f"/jobs/{job_id}/matches").json()

    assert body["reasoning"] == "The winning resume's explanation."
    assert body["hard_blocker"] is None
    assert body["insufficient_detail"] is False


def test_a_blocker_on_the_selected_profile_is_exposed() -> None:
    job_id = JobId(uuid4())
    selected = ProfileMatchScore(
        profile_id=ProfileId(uuid4()),
        resume_id=ResumeId(uuid4()),
        score=0.64,
        reasoning="Good overlap, one stated requirement unmet.",
        hard_blocker="Posting states a mandatory credential the profile does not list.",
        insufficient_detail=True,
    )
    client, _ = _client(
        FakeJobMatchRepository(
            {job_id: _job_match_with_scores(job_id, selected=selected, others=[])}
        )
    )

    body = client.get(f"/jobs/{job_id}/matches").json()

    assert body["hard_blocker"] == (
        "Posting states a mandatory credential the profile does not list."
    )
    assert body["insufficient_detail"] is True


def test_a_match_predating_the_explanation_fields_still_serializes() -> None:
    """The migration-free guarantee, end to end: `_job_match()`'s
    `ProfileMatchScore` is built without any of the three fields, exactly as
    a row persisted before this change deserializes. The response must fall
    through to the documented defaults rather than 500.
    """
    job_id = JobId(uuid4())
    client, _ = _client(FakeJobMatchRepository({job_id: _job_match(job_id)}))

    response = client.get(f"/jobs/{job_id}/matches")

    assert response.status_code == 200
    body = response.json()
    assert body["reasoning"] == ""
    assert body["hard_blocker"] is None
    assert body["insufficient_detail"] is False
    # The pre-existing fields are untouched by the projection.
    assert body["match_score"] == 0.91
    assert body["matched_skills"] == ["CAD"]
    assert body["missing_skills"] == ["Six Sigma"]


def test_defaults_are_used_when_the_selected_profile_has_no_score_entry() -> None:
    """A defensive case the projection must not crash on: `profile_scores`
    exists but contains no entry for `selected_profile_id`.
    """
    job_id = JobId(uuid4())
    orphan = ProfileMatchScore(
        profile_id=ProfileId(uuid4()),
        resume_id=ResumeId(uuid4()),
        score=0.4,
        reasoning="Belongs to a different profile.",
        hard_blocker="Not the selected profile's blocker.",
    )
    job_match = _job_match(job_id).model_copy(update={"profile_scores": [orphan]})
    client, _ = _client(FakeJobMatchRepository({job_id: job_match}))

    response = client.get(f"/jobs/{job_id}/matches")

    assert response.status_code == 200
    body = response.json()
    assert body["reasoning"] == ""
    assert body["hard_blocker"] is None
    assert body["insufficient_detail"] is False


def test_get_job_matches_returns_404_when_no_match_exists() -> None:
    client, _ = _client()

    response = client.get(f"/jobs/{uuid4()}/matches")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"
