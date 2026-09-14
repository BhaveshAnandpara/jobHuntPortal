"""Resume Tailoring Service — generation and the 5-point score gate
(docs/tailored-resume/TECH.md Task 3).

The whole feature rests on one promise from
docs/tailored-resume/PRODUCT.md: a tailored resume is only ever produced
when it scores at least 5 points higher than the original **on the same
yardstick the original was measured with**. That is why this module calls
`workflows.langgraph.job_matching.scoring.score_profile_with_llm`
completely unmodified rather than scoring the tailored content some other
way: a different or looser scoring standard for the tailored version would
make the 5-point promise meaningless.

Flow (one `tailor_resume` call):

    1. load the persisted `JobMatch` for `job_id`, verify it belongs to
       `user_id`, and read `match_score` (the base score) plus the
       `matched_skills`/`missing_skills` that analysis found
    2. load the winning `CandidateProfile` and run Task 2's transparent
       backfill over it (`ensure_enriched_profile`) so a resume uploaded
       before `contact`/`work_history` existed still tailors correctly
    3. LLM call #1 — `resume_tailoring.generation`: a new summary and one
       rewritten bullet list per position, applied onto a copy of the
       original `ResumeProfile` so every other field (contact, links,
       skills, education, projects, and each position's
       company/title/dates) is the candidate's own data, unchanged
    4. LLM call #2 — `score_profile_with_llm`, unmodified
    5. gate on `tailored_score - base_score >= 0.05` and return the
       decision plus, when it clears, the tailored content

Nothing is persisted here. Building the `.docx` (Task 4) and storing it
(Task 5) happen above this layer, only for an `improved=True` result.

**On cross-component reads.** `docs/architecture/dependency-graph.md`'s
rule that no component imports another component's module is honoured for
everything this module itself declares: the collaborators below are
`Protocol`s over shared domain types only, and the concrete repositories
are wired in `resume_tailoring.dependencies`, which documents its own
narrow, read-only grant — the same shape of deliberate, documented
exception `infrastructure.llm.user_client` already carries for reading
User Service's config row. `profiles.repository.to_resume_profile` is
imported directly because it is a pure, exported projection between two
*shared* types (`CandidateProfile` -> `ResumeProfile`) with no state of its
own; reimplementing it here would fork a shared contract's assembly rule,
which shared-types.md forbids more strongly than it forbids this import.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from infrastructure.llm import LLMClient
from infrastructure.llm.errors import LLMProviderError
from infrastructure.logging import format_context, get_logger
from profiles.repository import to_resume_profile
from resume_tailoring.errors import TailoringError
from resume_tailoring.generation import (
    apply_tailored_content,
    generate_tailored_content,
)
from shared.errors.codes import ErrorCode
from shared.types.domain.candidate_profile import CandidateProfile
from shared.types.domain.job import Job
from shared.types.domain.job_match import JobMatch
from shared.types.domain.user_preferences import UserPreferences
from shared.types.dto import NormalizedJob, ProfileMatchScore, ResumeProfile
from shared.types.ids import JobId, ProfileId, UserId
from workflows.langgraph.job_matching.scoring import score_profile_with_llm

logger = get_logger(__name__)

MINIMUM_SCORE_IMPROVEMENT = 0.05
"""The 5-point gate (scores are 0.0-1.0, so 5 points is 0.05).

docs/tailored-resume/PRODUCT.md: "the tailored version must score at least
5 percentage points higher than the original on this app's existing
match-scoring mechanism, or nothing gets created at all".
"""

_GATE_TOLERANCE = 1e-9
"""Slack for binary floating point, so a genuine exactly-5-point
improvement is never rejected by representation error.

This is not cosmetic. Both scores are ordinary floats, and for many pairs
the arithmetic lands just under the bar: `0.75 - 0.70` evaluates to
0.04999999999999993, so a strict `>= 0.05` would reject an improvement the
user can see is exactly five points. A tolerance nine decimal places below
the threshold admits that case and still rejects anything a person would
call "just under" — 0.0499 misses by five orders of magnitude more than the
tolerance.
"""


class ScoreFn(Protocol):
    """`score_profile_with_llm`'s signature, as a seam.

    Only so tests can substitute controlled scores; production always
    passes the real, unmodified function. It is a parameter rather than a
    module-level monkeypatch target so a test can never accidentally leave
    the real scorer replaced for another test.
    """

    def __call__(
        self,
        llm_client: LLMClient,
        job: NormalizedJob,
        profile: ResumeProfile,
        preferences: UserPreferences,
    ) -> ProfileMatchScore: ...


class JobMatchReader(Protocol):
    async def get_latest_for_job(self, job_id: JobId) -> JobMatch | None: ...


class CandidateProfileReader(Protocol):
    async def get(self, profile_id: ProfileId) -> CandidateProfile | None: ...


class JobReader(Protocol):
    async def get(self, job_id: JobId) -> Job | None: ...


class UserPreferencesReader(Protocol):
    async def get(self, user_id: UserId) -> UserPreferences | None: ...


class ProfileEnricher(Protocol):
    """The slice of `profiles.service.ProfileService` this component uses —
    Task 2's transparent, at-most-once backfill of `contact`/`work_history`
    for profiles that predate those fields.
    """

    async def ensure_enriched_profile(
        self, profile: CandidateProfile, *, client: LLMClient | None = None
    ) -> CandidateProfile: ...


class TailoringResult(BaseModel):
    """The outcome of one tailoring attempt.

    `improved=False` is a legitimate, successful outcome — "your current
    resume is already well-suited for this job" — not an error, and it
    carries no tailored content because none is worth keeping. Both
    variants carry `achieved_score` so the caller can log *why* an attempt
    did not clear the gate (TECH.md Task 5: "worth logging which, even
    without a persisted row, for support/debugging"); only the improved
    variant carries `tailored_profile`.
    """

    improved: bool
    base_score: float
    achieved_score: float
    tailored_profile: ResumeProfile | None = None
    # The skill-level breakdown behind the two scores. Both sides were
    # already computed before this feature existed and simply discarded
    # past the raw score delta: the `base_*` pair is what the persisted
    # `JobMatch` recorded when the original resume was scored, and the
    # `tailored_*` pair is what the *same, unmodified*
    # `score_profile_with_llm` call returned for the tailored content. No
    # extra LLM call, no second scoring standard, and no change to
    # workflows/langgraph/job_matching/scoring.py — carrying the lists out
    # is the whole change.
    #
    # Present on both outcomes on purpose: "your resume is already
    # well-suited" is exactly when a user most wants to see *which* skills
    # the job asks for and which are still missing.
    base_matched_skills: list[str] = []
    base_missing_skills: list[str] = []
    tailored_matched_skills: list[str] = []
    tailored_missing_skills: list[str] = []

    @classmethod
    def improvement(
        cls,
        *,
        base_score: float,
        achieved_score: float,
        tailored_profile: ResumeProfile,
        base_matched_skills: list[str] | None = None,
        base_missing_skills: list[str] | None = None,
        tailored_matched_skills: list[str] | None = None,
        tailored_missing_skills: list[str] | None = None,
    ) -> TailoringResult:
        return cls(
            improved=True,
            base_score=base_score,
            achieved_score=achieved_score,
            tailored_profile=tailored_profile,
            base_matched_skills=base_matched_skills or [],
            base_missing_skills=base_missing_skills or [],
            tailored_matched_skills=tailored_matched_skills or [],
            tailored_missing_skills=tailored_missing_skills or [],
        )

    @classmethod
    def no_improvement(
        cls,
        *,
        base_score: float,
        achieved_score: float,
        base_matched_skills: list[str] | None = None,
        base_missing_skills: list[str] | None = None,
        tailored_matched_skills: list[str] | None = None,
        tailored_missing_skills: list[str] | None = None,
    ) -> TailoringResult:
        return cls(
            improved=False,
            base_score=base_score,
            achieved_score=achieved_score,
            base_matched_skills=base_matched_skills or [],
            base_missing_skills=base_missing_skills or [],
            tailored_matched_skills=tailored_matched_skills or [],
            tailored_missing_skills=tailored_missing_skills or [],
        )


def clears_gate(base_score: float, tailored_score: float) -> bool:
    """Whether `tailored_score` beats `base_score` by at least 5 points."""
    return tailored_score - base_score >= MINIMUM_SCORE_IMPROVEMENT - _GATE_TOLERANCE


def to_normalized_job(job: Job) -> NormalizedJob:
    """`Job` -> `NormalizedJob`, the same one-way projection Job Ingestion
    Service applies at publish time (shared-types.md#transformation-rules).

    Needed because `score_profile_with_llm` takes a `NormalizedJob` — the
    canonical exchange shape — and the persisted row is a `Job`. Kept
    identical to `jobs.ingestion.service._to_normalized_job` (which is
    private to that module) so the tailored scoring call sees exactly the
    job text the original match scored.
    """
    return NormalizedJob(
        job_id=job.id,
        user_id=job.user_id,
        company=job.company,
        title=job.title,
        location=job.location,
        description=job.description_raw,
        extracted_skills=job.extracted_skills,
        experience_required=job.experience_required,
        source_type=job.source_type,
        source_url=job.source_url,
        discovered_at=job.discovered_at,
    )


class ResumeTailoringService:
    """Generates tailored resume content for one (job, user) pair and
    decides, on the app's own scoring mechanism, whether it is worth
    keeping.

    Collaborators are injected as `Protocol`s so this class has no
    compile-time dependency on any other component's modules and so tests
    can drive it with fakes; `resume_tailoring.dependencies` is the
    composition root that supplies the real ones.
    """

    def __init__(
        self,
        *,
        job_matches: JobMatchReader,
        profiles: CandidateProfileReader,
        jobs: JobReader,
        preferences: UserPreferencesReader,
        profile_service: ProfileEnricher,
        llm_client: LLMClient,
        score: ScoreFn = score_profile_with_llm,
    ) -> None:
        self._job_matches = job_matches
        self._profiles = profiles
        self._jobs = jobs
        self._preferences = preferences
        self._profile_service = profile_service
        self._llm_client = llm_client
        self._score = score

    async def tailor_resume(self, job_id: JobId, user_id: UserId) -> TailoringResult:
        """Run the full tailoring attempt for `job_id` on behalf of `user_id`.

        Raises `TailoringError` with `ErrorCode.NOT_FOUND` when the job has
        no match, when the match belongs to another user, or when the
        winning profile is gone; with `ErrorCode.LLM_PROVIDER_ERROR` when
        either LLM call fails. Persists nothing either way.
        """
        job_match = await self._load_job_match(job_id, user_id)
        job = await self._load_job(job_id, user_id)
        profile = await self._load_profile(job_match.selected_profile_id, user_id)

        # Task 2's transparent backfill. Called unconditionally on every
        # request: it is idempotent and costs nothing for an
        # already-enriched profile (`profile_needs_enrichment` returns False
        # and it returns the profile untouched, with no LLM call), and a
        # pre-feature profile would otherwise tailor into a resume with no
        # contact header and no work history at all.
        enriched = await self._profile_service.ensure_enriched_profile(
            profile, client=self._llm_client
        )
        base_profile = to_resume_profile(enriched)
        preferences = await self._preferences.get(user_id)

        base_score = job_match.match_score
        logger.info(
            "Resume tailoring started | %s",
            format_context(
                job_id=job_id,
                user_id=user_id,
                profile_id=base_profile.profile_id,
                base_score=base_score,
                missing_skills=len(job_match.missing_skills),
            ),
        )

        tailored_profile = self._generate(job, base_profile, job_match)
        tailored = self._score_tailored(job, tailored_profile, preferences, user_id)
        tailored_score = tailored.score
        # The same four lists travel on both outcomes below, so they are
        # gathered once here rather than assembled twice.
        breakdown = {
            "base_matched_skills": list(job_match.matched_skills),
            "base_missing_skills": list(job_match.missing_skills),
            "tailored_matched_skills": list(tailored.matched_skills),
            "tailored_missing_skills": list(tailored.missing_skills),
        }

        if not clears_gate(base_score, tailored_score):
            logger.info(
                "Resume tailoring produced no significant improvement | %s",
                format_context(
                    job_id=job_id,
                    user_id=user_id,
                    base_score=base_score,
                    achieved_score=tailored_score,
                    threshold=MINIMUM_SCORE_IMPROVEMENT,
                ),
            )
            return TailoringResult.no_improvement(
                base_score=base_score, achieved_score=tailored_score, **breakdown
            )

        logger.info(
            "Resume tailoring cleared the improvement gate | %s",
            format_context(
                job_id=job_id,
                user_id=user_id,
                base_score=base_score,
                achieved_score=tailored_score,
            ),
        )
        return TailoringResult.improvement(
            base_score=base_score,
            achieved_score=tailored_score,
            tailored_profile=tailored_profile,
            **breakdown,
        )

    # -- steps ------------------------------------------------------------

    async def _load_job_match(self, job_id: JobId, user_id: UserId) -> JobMatch:
        job_match = await self._job_matches.get_latest_for_job(job_id)
        # A match owned by someone else is reported exactly like no match at
        # all — see `TailoringError`'s docstring: distinguishing the two
        # would confirm another user's job exists and has been matched.
        if job_match is None or job_match.user_id != user_id:
            raise TailoringError(
                ErrorCode.NOT_FOUND, f"no job match found for job {job_id}"
            )
        return job_match

    async def _load_job(self, job_id: JobId, user_id: UserId) -> NormalizedJob:
        job = await self._jobs.get(job_id)
        if job is None or job.user_id != user_id:
            raise TailoringError(ErrorCode.NOT_FOUND, f"no job found with id {job_id}")
        return to_normalized_job(job)

    async def _load_profile(
        self, profile_id: ProfileId, user_id: UserId
    ) -> CandidateProfile:
        profile = await self._profiles.get(profile_id)
        if profile is None or profile.user_id != user_id:
            raise TailoringError(
                ErrorCode.NOT_FOUND,
                f"the profile selected for this job ({profile_id}) no longer exists",
            )
        return profile

    def _generate(
        self, job: NormalizedJob, profile: ResumeProfile, job_match: JobMatch
    ) -> ResumeProfile:
        """LLM call #1, and the profile that both scoring and rendering see.

        `generate_tailored_content` asks the model for a summary and one
        bullet list per position — nothing else — and
        `apply_tailored_content` lays exactly those two things over a copy
        of `profile`. The `ResumeProfile` returned here is therefore the
        original in every other respect, which is precisely what
        `document.build_resume_document` needs: one shared visual template,
        populated from this candidate's own real record.

        It raises `TailoringError` itself when the model's bullet lists do
        not line up one-to-one with `profile.work_history` — deliberately
        not caught here, since misaligning bullets across employers is the
        one outcome worse than failing the request.
        """
        try:
            content = generate_tailored_content(
                self._llm_client,
                job,
                profile,
                matched_skills=job_match.matched_skills,
                missing_skills=job_match.missing_skills,
            )
        except LLMProviderError as error:
            logger.exception(
                "Tailored content generation failed | %s",
                format_context(job_id=job.job_id, user_id=job.user_id),
            )
            raise TailoringError(
                ErrorCode.LLM_PROVIDER_ERROR,
                f"could not generate tailored resume content: {error}",
            ) from error
        return apply_tailored_content(profile, content)

    def _score_tailored(
        self,
        job: NormalizedJob,
        tailored_profile: ResumeProfile,
        preferences: UserPreferences | None,
        user_id: UserId,
    ) -> ProfileMatchScore:
        """LLM call #2 — the *unmodified* job-matching scorer.

        Returns the whole `ProfileMatchScore` rather than just `.score`:
        the scorer already reports `matched_skills`/`missing_skills` for the
        tailored content, and throwing them away here is the only reason the
        caller could not previously say *why* the score moved.

        A user with no saved search preferences gets a neutral default,
        mirroring `matching.consumers._empty_preferences`: tailoring must
        not fail just because someone never filled in a preferences form,
        and an empty `UserPreferences` scores `location_preference_fit`
        neutrally rather than penalizing them.
        """
        try:
            result = self._score(
                self._llm_client,
                job,
                tailored_profile,
                preferences or _empty_preferences(user_id),
            )
        except LLMProviderError as error:
            logger.exception(
                "Scoring of tailored content failed | %s",
                format_context(job_id=job.job_id, user_id=user_id),
            )
            raise TailoringError(
                ErrorCode.LLM_PROVIDER_ERROR,
                f"could not score the tailored resume: {error}",
            ) from error
        return result


def _empty_preferences(user_id: UserId) -> UserPreferences:
    from datetime import UTC, datetime
    from uuid import uuid4

    from shared.types.ids import UserPreferencesId

    return UserPreferences(
        id=UserPreferencesId(uuid4()),
        user_id=user_id,
        updated_at=datetime.now(UTC),
    )


async def tailor_resume(job_id: JobId, user_id: UserId) -> TailoringResult:
    """Tailor `user_id`'s matched resume for `job_id`, self-contained.

    The entry point Task 6's `POST /jobs/{job_id}/tailored-resume` route
    calls. Resolves the caller's own LLM client
    (`get_user_llm_client` — raising `LlmNotConfiguredError` when they have
    no validated provider, which the route maps to the app's standard
    `LLM_NOT_CONFIGURED` gating response) and opens one session for the
    whole attempt, so the profile enrichment
    (`ProfileService.ensure_enriched_profile`) commits with it.

    Imports its collaborators lazily, via `resume_tailoring.dependencies`,
    so importing this module requires no database, broker, or LLM provider.
    """
    from infrastructure.llm import get_user_llm_client
    from resume_tailoring.db import tailoring_session_scope
    from resume_tailoring.dependencies import build_service

    llm_client = await get_user_llm_client(user_id)
    async with tailoring_session_scope() as session:
        service = build_service(session, llm_client)
        return await service.tailor_resume(job_id, user_id)


__all__ = [
    "MINIMUM_SCORE_IMPROVEMENT",
    "ResumeTailoringService",
    "TailoringResult",
    "clears_gate",
    "tailor_resume",
    "to_normalized_job",
]
