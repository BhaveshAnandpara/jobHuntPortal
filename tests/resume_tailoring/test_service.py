"""`ResumeTailoringService.tailor_resume` — the 5-point gate, the Task 2
backfill hand-off, and the ownership check.

The scoring call is injected (`ResumeTailoringService(score=...)`) rather
than monkeypatched, so these tests control the tailored score exactly while
`workflows.langgraph.job_matching.scoring.score_profile_with_llm` itself
stays untouched — that the production default *is* that very function is
asserted separately, in `test_gate.py`.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from resume_tailoring.errors import TailoringError
from resume_tailoring.generation import TailoredProfileContent
from resume_tailoring.service import (
    MINIMUM_SCORE_IMPROVEMENT,
    ResumeTailoringService,
)
from shared.errors.codes import ErrorCode
from shared.types.dto import ContactInfo, ProfileMatchScore, WorkExperienceEntry
from shared.types.ids import JobId, UserId
from tests.resume_tailoring.conftest import (
    FakeCandidateProfileReader,
    FakeJobMatchReader,
    FakeJobReader,
    FakeLLMClient,
    FakeUserPreferencesReader,
    SpyProfileService,
    make_candidate_profile,
    make_job,
    make_job_match,
    make_user_preferences,
)

pytestmark = pytest.mark.asyncio

BASE_SCORE = 0.62

# The whole of what the model is asked for: a summary, and one bullet list
# per position in the fixture's work history (which has exactly one).
TAILORED_SUMMARY = "Six years of high-dependency nursing, including ventilated patients."
TAILORED_BULLET = "Cared for ventilated post-operative patients"

TAILORED_CONTENT = TailoredProfileContent(
    summary=TAILORED_SUMMARY,
    bullets_by_job=[[TAILORED_BULLET]],
)


def provider_error(message: str) -> LLMProviderError:
    return LLMProviderError(
        LLMFailureReason.TIMEOUT, message, provider="fake-llm", model="fake-model"
    )


class _Harness:
    """One assembled service plus the fakes behind it, so each test reads
    as "arrange one thing, assert one thing".
    """

    def __init__(
        self,
        *,
        tailored_score: float = 1.0,
        llm_client: object | None = None,
        enriched: object | None = None,
        tailored_matched_skills: list[str] | None = None,
        tailored_missing_skills: list[str] | None = None,
        **profile_overrides: object,
    ) -> None:
        self.user_id = UserId(uuid4())
        self.job = make_job(self.user_id)
        self.profile = make_candidate_profile(self.user_id, **profile_overrides)
        self.job_match = make_job_match(
            self.user_id, self.job.id, self.profile, match_score=BASE_SCORE
        )
        self.tailored_score = tailored_score

        self.job_matches = FakeJobMatchReader({self.job.id: self.job_match})
        self.profiles = FakeCandidateProfileReader({self.profile.id: self.profile})
        self.jobs = FakeJobReader({self.job.id: self.job})
        self.preferences = FakeUserPreferencesReader(
            {self.user_id: make_user_preferences(self.user_id)}
        )
        self.profile_service = SpyProfileService(enriched=enriched)
        self.llm_client = llm_client or FakeLLMClient(TAILORED_CONTENT)
        self.scored: list[object] = []

        self.tailored_matched_skills = tailored_matched_skills or []
        self.tailored_missing_skills = tailored_missing_skills or []

        def score(llm_client, job, profile, preferences) -> ProfileMatchScore:
            self.scored.append((llm_client, job, profile, preferences))
            return ProfileMatchScore(
                profile_id=profile.profile_id,
                resume_id=profile.resume_id,
                score=self.tailored_score,
                matched_skills=list(self.tailored_matched_skills),
                missing_skills=list(self.tailored_missing_skills),
            )

        self.service = ResumeTailoringService(
            job_matches=self.job_matches,
            profiles=self.profiles,
            jobs=self.jobs,
            preferences=self.preferences,
            profile_service=self.profile_service,
            llm_client=self.llm_client,
            score=score,
        )

    def store_profile(self, profile) -> None:
        """Replace the stored `CandidateProfile` (same id) — how a test sets
        up a pre-feature row without rebuilding the whole harness.
        """
        self.profiles.by_id = {profile.id: profile}

    async def run(self, *, job_id: JobId | None = None, user_id: UserId | None = None):
        return await self.service.tailor_resume(
            job_id or self.job.id, user_id or self.user_id
        )


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


async def test_exactly_five_points_better_clears_the_gate() -> None:
    """PRODUCT.md: "at least 5 percentage points higher". At exactly the
    bar, the user gets their document — so the boundary is tested, and with
    it the float-representation slack `clears_gate` allows for
    (`0.62 + 0.05` does not subtract back to exactly `0.05` in binary).
    """
    harness = _Harness(tailored_score=BASE_SCORE + MINIMUM_SCORE_IMPROVEMENT)

    result = await harness.run()

    assert result.improved is True
    assert result.base_score == BASE_SCORE
    assert result.achieved_score == pytest.approx(BASE_SCORE + MINIMUM_SCORE_IMPROVEMENT)
    assert result.tailored_profile is not None


async def test_just_under_five_points_does_not_clear_the_gate() -> None:
    harness = _Harness(tailored_score=BASE_SCORE + 0.0499)

    result = await harness.run()

    assert result.improved is False
    assert result.base_score == BASE_SCORE
    assert result.tailored_profile is None


async def test_a_worse_tailored_score_does_not_clear_the_gate() -> None:
    harness = _Harness(tailored_score=BASE_SCORE - 0.2)

    result = await harness.run()

    assert result.improved is False
    assert result.tailored_profile is None


async def test_a_large_improvement_clears_the_gate() -> None:
    harness = _Harness(tailored_score=0.91)

    result = await harness.run()

    assert result.improved is True
    assert result.achieved_score == 0.91


async def test_nothing_is_persisted_for_either_outcome() -> None:
    """Task 3 returns a decision and content; persistence is Task 5's. The
    only write anywhere on this path is Resume/Profile Service's own
    enrichment of its own row.
    """
    for tailored_score in (0.95, BASE_SCORE):
        harness = _Harness(tailored_score=tailored_score)

        await harness.run()

        # The readers are read-only by construction; assert the service was
        # never handed anything that could write.
        assert not hasattr(harness.job_matches, "add")
        assert not hasattr(harness.profiles, "add")


# ---------------------------------------------------------------------------
# The scoring call is the real, unmodified one
# ---------------------------------------------------------------------------


async def test_the_tailored_profile_is_what_gets_scored() -> None:
    """Scoring the original by mistake would make every attempt fail the
    gate; scoring something other than what is returned would make the
    reported `achieved_score` a lie.
    """
    harness = _Harness(tailored_score=0.95)

    result = await harness.run()

    (_, scored_job, scored_profile, scored_preferences) = harness.scored[0]
    assert scored_profile == result.tailored_profile
    assert scored_profile.summary == TAILORED_SUMMARY
    assert scored_profile.work_history[0].bullets == [TAILORED_BULLET]
    assert scored_job.job_id == harness.job.id
    assert scored_job.description == harness.job.description_raw
    assert scored_preferences.user_id == harness.user_id


async def test_a_user_without_preferences_still_gets_scored() -> None:
    """Tailoring must not fail because someone never filled in the search
    preferences form — the same neutral default normal matching uses.
    """
    harness = _Harness(tailored_score=0.95)
    harness.preferences.by_user.clear()

    result = await harness.run()

    assert result.improved is True
    (_, _, _, scored_preferences) = harness.scored[0]
    assert scored_preferences.user_id == harness.user_id
    assert scored_preferences.target_roles == []


# ---------------------------------------------------------------------------
# Task 2's transparent backfill is actually invoked
# ---------------------------------------------------------------------------


async def test_ensure_enriched_profile_is_called_before_generation() -> None:
    """A profile uploaded before `contact`/`work_history` existed must be
    backfilled first, or the tailored resume has no contact header and no
    work history at all. Proven, not assumed.
    """
    harness = _Harness(tailored_score=0.95)
    harness.store_profile(
        harness.profile.model_copy(update={"contact": None, "work_history": []})
    )
    harness.profile_service._enriched = harness.profile

    await harness.run()

    assert len(harness.profile_service.calls) == 1
    called_with, client = harness.profile_service.calls[0]
    assert called_with.id == harness.job_match.selected_profile_id
    assert called_with.contact is None, "the un-enriched row is what gets handed over"
    assert client is harness.llm_client, "enrichment must run on the caller's own LLM client"


async def test_tailoring_works_from_the_enriched_profile_not_the_stored_one() -> None:
    """Calling the backfill is not enough — its *result* has to be what
    feeds the prompt, otherwise a pre-feature profile still tailors into an
    empty document.
    """
    harness = _Harness(tailored_score=0.95)
    harness.store_profile(
        harness.profile.model_copy(update={"contact": None, "work_history": []})
    )
    enriched = harness.profile.model_copy(
        update={
            "contact": ContactInfo(full_name="Aoife Devlin", email="aoife@example.com"),
            "work_history": [
                WorkExperienceEntry(
                    company="Backfilled Trust",
                    title="Staff Nurse",
                    start_date="Aug 2019",
                    bullets=["A bullet recovered by the backfill"],
                )
            ],
        }
    )
    harness.profile_service._enriched = enriched

    result = await harness.run()

    prompt = harness.llm_client.prompts[0]
    assert "Backfilled Trust" in prompt
    assert "A bullet recovered by the backfill" in prompt
    assert result.tailored_profile is not None
    assert result.tailored_profile.contact == enriched.contact


async def test_an_already_enriched_profile_still_passes_through_the_backfill() -> None:
    """`ensure_enriched_profile` is idempotent and cheap for an
    already-enriched profile, so it is called unconditionally rather than
    guarded by a second copy of the "does this need enrichment?" rule.
    """
    harness = _Harness(tailored_score=0.95)

    await harness.run()

    assert len(harness.profile_service.calls) == 1


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


async def test_another_users_job_match_is_not_readable() -> None:
    """The check that stops one user's tailoring request from reading
    another user's `JobMatch` — its score, its matched/missing skills, and
    the profile it selected.
    """
    harness = _Harness()
    intruder = UserId(uuid4())

    with pytest.raises(TailoringError) as excinfo:
        await harness.run(user_id=intruder)

    assert excinfo.value.code is ErrorCode.NOT_FOUND
    assert not harness.llm_client.prompts, "no LLM call may run for a rejected request"
    assert not harness.profile_service.calls


async def test_an_unknown_job_is_reported_identically_to_someone_elses() -> None:
    """A distinguishable response would confirm that another user's job
    exists and has been matched — so both cases are `NOT_FOUND` with the
    same message shape.
    """
    harness = _Harness()
    unknown = JobId(uuid4())

    with pytest.raises(TailoringError) as missing:
        await harness.run(job_id=unknown)
    with pytest.raises(TailoringError) as not_mine:
        await harness.run(user_id=UserId(uuid4()))

    assert missing.value.code is not_mine.value.code is ErrorCode.NOT_FOUND
    assert missing.value.message.startswith("no job match found for job")
    assert not_mine.value.message.startswith("no job match found for job")


async def test_a_job_row_belonging_to_another_user_is_rejected() -> None:
    """Belt and braces: even if a `JobMatch` somehow named a job owned by
    someone else, the job itself is re-checked before its text reaches a
    prompt.
    """
    harness = _Harness()
    harness.jobs.by_id = {
        harness.job.id: harness.job.model_copy(update={"user_id": UserId(uuid4())})
    }

    with pytest.raises(TailoringError) as excinfo:
        await harness.run()

    assert excinfo.value.code is ErrorCode.NOT_FOUND
    assert not harness.llm_client.prompts


async def test_a_missing_selected_profile_is_reported_clearly() -> None:
    harness = _Harness()
    harness.profiles.by_id.clear()

    with pytest.raises(TailoringError) as excinfo:
        await harness.run()

    assert excinfo.value.code is ErrorCode.NOT_FOUND
    assert "no longer exists" in excinfo.value.message


async def test_a_profile_belonging_to_another_user_is_rejected() -> None:
    harness = _Harness()
    harness.profiles.by_id = {
        harness.profile.id: harness.profile.model_copy(update={"user_id": UserId(uuid4())})
    }

    with pytest.raises(TailoringError) as excinfo:
        await harness.run()

    assert excinfo.value.code is ErrorCode.NOT_FOUND


# ---------------------------------------------------------------------------
# LLM failures
# ---------------------------------------------------------------------------


async def test_a_generation_failure_is_normalized() -> None:
    harness = _Harness(llm_client=FakeLLMClient(provider_error("provider timed out")))

    with pytest.raises(TailoringError) as excinfo:
        await harness.run()

    assert excinfo.value.code is ErrorCode.LLM_PROVIDER_ERROR
    assert "could not generate tailored resume content" in excinfo.value.message


async def test_a_scoring_failure_is_normalized() -> None:
    harness = _Harness()

    def failing_score(*_args: object, **_kwargs: object):
        raise provider_error("provider refused")

    harness.service._score = failing_score

    with pytest.raises(TailoringError) as excinfo:
        await harness.run()

    assert excinfo.value.code is ErrorCode.LLM_PROVIDER_ERROR
    assert "could not score the tailored resume" in excinfo.value.message


# ---------------------------------------------------------------------------
# Skill breakdown — the data behind the two scores
# ---------------------------------------------------------------------------

# What the persisted `JobMatch` recorded for the ORIGINAL resume — see
# `tests/resume_tailoring/conftest.py::make_job_match`.
BASE_MATCHED = ["Ventilator management", "Patient assessment"]
BASE_MISSING = ["ALS certification", "Continuous renal replacement therapy"]


async def test_an_improved_result_carries_both_sides_of_the_breakdown() -> None:
    """The base pair comes from the persisted `JobMatch`; the tailored pair
    comes from the same unmodified scorer that produced `achieved_score`.
    Both were already computed — this proves they now reach the caller."""
    harness = _Harness(
        tailored_score=0.95,
        tailored_matched_skills=[
            "Ventilator management",
            "Patient assessment",
            "ALS certification",
        ],
        tailored_missing_skills=["Continuous renal replacement therapy"],
    )

    result = await harness.run()

    assert result.improved is True
    assert result.base_matched_skills == BASE_MATCHED
    assert result.base_missing_skills == BASE_MISSING
    assert result.tailored_matched_skills == [
        "Ventilator management",
        "Patient assessment",
        "ALS certification",
    ]
    assert result.tailored_missing_skills == ["Continuous renal replacement therapy"]


async def test_a_no_improvement_result_carries_the_breakdown_too() -> None:
    """"Already well-suited" is exactly when a user wants to know which
    skills are still missing, so the lists travel on this outcome as well."""
    harness = _Harness(
        tailored_score=BASE_SCORE + 0.01,
        tailored_matched_skills=["Ventilator management"],
        tailored_missing_skills=["ALS certification"],
    )

    result = await harness.run()

    assert result.improved is False
    assert result.tailored_profile is None
    assert result.base_matched_skills == BASE_MATCHED
    assert result.base_missing_skills == BASE_MISSING
    assert result.tailored_matched_skills == ["Ventilator management"]
    assert result.tailored_missing_skills == ["ALS certification"]


async def test_the_breakdown_shows_what_tailoring_newly_emphasized() -> None:
    """The user-facing question — "which skills did tailoring pick up?" — is
    answerable from the four lists without any further computation here."""
    harness = _Harness(
        tailored_score=0.95,
        tailored_matched_skills=[*BASE_MATCHED, "ALS certification"],
        tailored_missing_skills=["Continuous renal replacement therapy"],
    )

    result = await harness.run()

    newly_emphasized = [
        skill
        for skill in result.tailored_matched_skills
        if skill not in result.base_matched_skills
    ]
    assert newly_emphasized == ["ALS certification"]
    assert "ALS certification" in result.base_missing_skills


async def test_the_breakdown_is_empty_lists_when_the_scorer_reports_none() -> None:
    """Never `None`: the four fields are lists on both outcomes so no caller
    has to null-check them."""
    harness = _Harness(tailored_score=0.95)
    harness.job_match.matched_skills = []
    harness.job_match.missing_skills = []

    result = await harness.run()

    assert result.tailored_matched_skills == []
    assert result.tailored_missing_skills == []
    assert result.base_matched_skills == []
    assert result.base_missing_skills == []


async def test_the_breakdown_does_not_alias_the_job_match_lists() -> None:
    """The result must own its lists — mutating one afterwards must not
    reach back into the persisted `JobMatch` the service read."""
    harness = _Harness(tailored_score=0.95)

    result = await harness.run()
    result.base_matched_skills.append("Fabricated skill")

    assert "Fabricated skill" not in harness.job_match.matched_skills


async def test_the_locked_scorer_is_still_called_exactly_once() -> None:
    """The breakdown costs no extra LLM call — it is data the single
    existing scoring call already returned."""
    harness = _Harness(
        tailored_score=0.95, tailored_matched_skills=["Ventilator management"]
    )

    await harness.run()

    assert len(harness.scored) == 1
