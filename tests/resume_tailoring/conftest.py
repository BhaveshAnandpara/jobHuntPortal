"""Fixtures, fakes, and domain factories for Resume Tailoring Service tests.

Everything here runs against fakes — no PostgreSQL, Kafka broker, or LLM
server is required to run `tests/resume_tailoring`. The LLM is faked at the
`LLMClient.complete_structured` seam, the same boundary
`tests/matching/conftest.py::FakeLLMClient` fakes, so the prompt text that
reaches a provider in production is exactly the text these tests assert on.

The fixtures are deliberately profession-neutral in the other direction
from the job-matching tests' mechanical-engineering fixtures: this one is a
hospital ward nurse, so a tailoring rule that only works for a technology
resume would show up here (about_project.md; CLAUDE.md rules 3-4).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from shared.types.domain.candidate_profile import CandidateProfile
from shared.types.domain.job import Job
from shared.types.domain.job_match import JobMatch
from shared.types.domain.user_preferences import UserPreferences
from shared.types.dto import (
    ContactInfo,
    EducationEntry,
    ProfileMatchScore,
    WorkExperienceEntry,
)
from shared.types.enums import (
    JobProcessingStatus,
    JobSourceType,
    MatchRecommendation,
    ProfileStatus,
)
from shared.types.ids import (
    JobId,
    JobMatchId,
    ProfileId,
    ResumeId,
    UserId,
    UserPreferencesId,
)


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Domain factories
# ---------------------------------------------------------------------------


def make_job(user_id: UserId, **overrides: Any) -> Job:
    fields: dict[str, Any] = {
        "id": JobId(uuid4()),
        "user_id": user_id,
        "source_type": JobSourceType.MANUAL_URL,
        "source_url": "https://careers.example.org/postings/nurse-icu",
        "company": "Riverside General Hospital",
        "title": "ICU Staff Nurse",
        "location": "Leeds, UK",
        "description_raw": (
            "Provide direct nursing care in a 22-bed intensive care unit. "
            "Manage ventilated patients, titrate vasoactive infusions, and "
            "lead family communication during end-of-life care. ALS "
            "certification required; experience with continuous renal "
            "replacement therapy preferred."
        ),
        "extracted_skills": [
            "Critical care nursing",
            "Ventilator management",
            "ALS certification",
            "Continuous renal replacement therapy",
        ],
        "experience_required": "3+ years",
        "processing_status": JobProcessingStatus.MATCHED,
        "discovered_at": now(),
    }
    fields.update(overrides)
    return Job(**fields)


def make_candidate_profile(user_id: UserId, **overrides: Any) -> CandidateProfile:
    fields: dict[str, Any] = {
        "id": ProfileId(uuid4()),
        "user_id": user_id,
        "resume_id": ResumeId(uuid4()),
        "title": "Registered Nurse",
        "summary": "Ward nurse with high-dependency experience.",
        "contact": ContactInfo(
            full_name="Aoife Devlin",
            email="aoife.devlin@example.com",
            phone="+44 7700 900123",
            location="Leeds, UK",
        ),
        "skills": [
            "Patient assessment",
            "Ventilator management",
            "Medication administration",
            "Critical care nursing",
        ],
        "experience_years": 6.0,
        "seniority": "Senior",
        "education": [
            EducationEntry(
                institution="University of Leeds",
                degree="BSc",
                field_of_study="Adult Nursing",
                graduation_year=2017,
            )
        ],
        "work_history": [
            WorkExperienceEntry(
                company="St Bede's Trust",
                title="Staff Nurse, High Dependency Unit",
                start_date="Aug 2019",
                end_date=None,
                bullets=[
                    "Cared for post-operative patients requiring ventilatory support",
                    "Precepted four newly qualified nurses through their first year",
                ],
            )
        ],
        "certifications": ["Immediate Life Support"],
        "projects": ["Redesigned the unit's handover checklist"],
        "industries": ["Healthcare"],
        "target_roles": ["Critical Care Nurse"],
        "status": ProfileStatus.ACTIVE,
        "generated_at": now(),
    }
    fields.update(overrides)
    return CandidateProfile(**fields)


def make_job_match(
    user_id: UserId, job_id: JobId, profile: CandidateProfile, **overrides: Any
) -> JobMatch:
    fields: dict[str, Any] = {
        "id": JobMatchId(uuid4()),
        "job_id": job_id,
        "user_id": user_id,
        "selected_profile_id": profile.id,
        "selected_resume_id": profile.resume_id,
        "match_score": 0.62,
        "matched_skills": ["Ventilator management", "Patient assessment"],
        "missing_skills": [
            "ALS certification",
            "Continuous renal replacement therapy",
        ],
        "recommendation": MatchRecommendation.SHORTLIST,
        "profile_scores": [],
        "matched_at": now(),
    }
    fields.update(overrides)
    return JobMatch(**fields)


def make_user_preferences(user_id: UserId, **overrides: Any) -> UserPreferences:
    fields: dict[str, Any] = {
        "id": UserPreferencesId(uuid4()),
        "user_id": user_id,
        "target_roles": ["Critical Care Nurse"],
        "target_locations": ["Leeds"],
        "updated_at": now(),
    }
    fields.update(overrides)
    return UserPreferences(**fields)


def make_profile_match_score(profile: CandidateProfile, score: float) -> ProfileMatchScore:
    return ProfileMatchScore(
        profile_id=profile.id, resume_id=profile.resume_id, score=score
    )


# ---------------------------------------------------------------------------
# Fake readers — same method surface as the real repositories the
# composition root (resume_tailoring/dependencies.py) binds, backed by
# plain dicts.
# ---------------------------------------------------------------------------


class FakeJobMatchReader:
    def __init__(self, matches: dict[JobId, JobMatch] | None = None) -> None:
        self.by_job: dict[JobId, JobMatch] = dict(matches or {})
        self.requested: list[JobId] = []

    async def get_latest_for_job(self, job_id: JobId) -> JobMatch | None:
        self.requested.append(job_id)
        return self.by_job.get(job_id)


class FakeCandidateProfileReader:
    def __init__(self, profiles: dict[ProfileId, CandidateProfile] | None = None) -> None:
        self.by_id: dict[ProfileId, CandidateProfile] = dict(profiles or {})

    async def get(self, profile_id: ProfileId) -> CandidateProfile | None:
        return self.by_id.get(profile_id)


class FakeJobReader:
    def __init__(self, jobs: dict[JobId, Job] | None = None) -> None:
        self.by_id: dict[JobId, Job] = dict(jobs or {})

    async def get(self, job_id: JobId) -> Job | None:
        return self.by_id.get(job_id)


class FakeUserPreferencesReader:
    def __init__(self, preferences: dict[UserId, UserPreferences] | None = None) -> None:
        self.by_user: dict[UserId, UserPreferences] = dict(preferences or {})

    async def get(self, user_id: UserId) -> UserPreferences | None:
        return self.by_user.get(user_id)


class SpyProfileService:
    """Records every `ensure_enriched_profile` call and, by default, returns
    an enriched copy — so a test can prove both *that* Task 2's backfill was
    invoked and that tailoring then worked from its result rather than from
    the un-enriched profile it was handed.
    """

    def __init__(self, enriched: CandidateProfile | None = None) -> None:
        self._enriched = enriched
        self.calls: list[tuple[CandidateProfile, object]] = []

    async def ensure_enriched_profile(
        self, profile: CandidateProfile, *, client: object | None = None
    ) -> CandidateProfile:
        self.calls.append((profile, client))
        return self._enriched if self._enriched is not None else profile


# ---------------------------------------------------------------------------
# Fake LLMClient — only `.complete_structured` is ever called, by
# `resume_tailoring.generation` (and, in production, by the unmodified
# `score_profile_with_llm`).
# ---------------------------------------------------------------------------


class FakeLLMClient:
    """`LLMClient`-shaped fake that returns `result` (or raises it, when it
    is an exception) and records every prompt it was given on `.prompts`.

    Same shape and purpose as `tests/matching/conftest.py::FakeLLMClient`,
    trimmed to one scripted result because this component makes exactly one
    generation call per request; the scoring call is injected separately
    (`ResumeTailoringService(score=...)`) so a test can control the two
    scores without scripting two LLM responses.
    """

    def __init__(self, result: object) -> None:
        self._result = result
        self.prompts: list[str] = []
        self.systems: list[str | None] = []
        self.schemas: list[type] = []

    def complete_structured(self, prompt, schema, *, system=None, options=None):
        self.prompts.append(prompt)
        self.systems.append(system)
        self.schemas.append(schema)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def naming_override(
    *, full_name: str | None = None, company: str | None = None, role: str | None = None
):
    """A `get_document_naming` override for the route harnesses.

    The real dependency reads Job Ingestion's `jobs` table and Resume/Profile
    Service's `candidate_profiles` table to name the downloaded file. Route
    tests stand up only `tailored_resumes` (deliberately — see
    `tests/resume_tailoring/test_repository.py` on why a blanket
    `create_all` is unsafe here), so they substitute this instead. The real
    resolver has its own test against real rows in
    `tests/resume_tailoring/test_naming.py`.
    """
    from resume_tailoring.api import DocumentNaming

    async def resolve(record: object) -> Any:
        return DocumentNaming(full_name=full_name, company=company, role=role)

    return lambda: resolve


__all__ = [
    "FakeCandidateProfileReader",
    "FakeJobMatchReader",
    "FakeJobReader",
    "FakeLLMClient",
    "FakeUserPreferencesReader",
    "SpyProfileService",
    "make_candidate_profile",
    "make_job",
    "make_job_match",
    "make_profile_match_score",
    "make_user_preferences",
    "naming_override",
    "now",
]
