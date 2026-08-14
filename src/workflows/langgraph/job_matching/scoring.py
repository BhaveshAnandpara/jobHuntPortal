"""LLM-backed semantic scoring for one (job, profile) pair.

Internal to the Job Matching LangGraph workflow. `ProfileScoringOutput`
below is an internal LLM-call contract, not a shared type — only
`ProfileMatchScore` (`shared.types.dto`) crosses the component boundary,
via `to_profile_match_score()`. Defining this schema locally is consistent
with the task brief: "you may define this schema type locally in your own
owned files — it's an internal LLM-call contract, not a new shared type."

Scoring is deliberately profession-independent: nothing here branches on a
profile's title, industry, or any specific skill/domain (about_project.md's
core philosophy; CLAUDE.md rules 3-4 — the system must not be hard-coded
for software engineers or any other profession). Discrimination between
candidate profiles comes entirely from the LLM's semantic read of the job's
free-text description/skills against each profile's free-text
skills/title/experience/seniority/education/industries/target_roles — the
same prompt and schema are used for a mechanical engineering job as for an
HR job or a software engineering job (see about_project.md's own
"AI Engineer / Java Backend / Full-Stack" and "Mechanical Design / CAD /
Manufacturing" resume-set examples).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from infrastructure.llm import LLMClient
from shared.types.domain.user_preferences import UserPreferences
from shared.types.dto import NormalizedJob, ProfileMatchScore, ResumeProfile

_SYSTEM_PROMPT = (
    "You are an expert, profession-agnostic recruiting analyst. You "
    "evaluate how well ONE candidate profile fits ONE job posting, for any "
    "profession (software engineering, mechanical/civil engineering, HR, "
    "product management, data analytics, finance, design, skilled trades, "
    "students/freshers, and more). Never assume the profession from the "
    "job title alone, and never apply a rule specific to one profession — "
    "read the actual skills, responsibilities, and requirements described "
    "and judge fit on their merits."
)


class ProfileScoringOutput(BaseModel):
    """The structured shape requested of the LLM for one (job, profile)
    pair.

    The per-factor fields (`role_relevance` .. `location_preference_fit`)
    exist so the LLM is forced to reason about each factor named in
    about_project.md's "Job Matching" section (required/preferred skills,
    experience level, domain relevance, role similarity, seniority,
    location, candidate preferences, resume relevance) individually before
    committing to `overall_score`, rather than emitting one unexplained
    number — this is what makes the result a genuine semantic judgement
    instead of a keyword match.
    """

    role_relevance: float = Field(ge=0.0, le=1.0)
    skills_fit: float = Field(ge=0.0, le=1.0)
    experience_fit: float = Field(ge=0.0, le=1.0)
    domain_fit: float = Field(ge=0.0, le=1.0)
    seniority_fit: float = Field(ge=0.0, le=1.0)
    location_preference_fit: float = Field(ge=0.0, le=1.0)
    overall_score: float = Field(ge=0.0, le=1.0)
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    reasoning: str = ""


def _joined(values: list[str], *, empty: str) -> str:
    return ", ".join(values) if values else empty


def build_scoring_prompt(
    job: NormalizedJob, profile: ResumeProfile, preferences: UserPreferences
) -> str:
    education_lines = (
        "\n".join(
            f"- {entry.degree or 'Degree'} in "
            f"{entry.field_of_study or 'unspecified field'} — {entry.institution}"
            for entry in profile.education
        )
        or "(none listed)"
    )
    remote_preference = (
        preferences.remote_preference.value
        if preferences.remote_preference is not None
        else "no preference stated"
    )

    return (
        "Evaluate how well the CANDIDATE PROFILE below fits the JOB POSTING "
        "below. This platform serves every profession — judge fit purely "
        "from the text given, never from an assumption about what "
        "profession the job title implies.\n\n"
        "JOB POSTING\n"
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location or 'not specified'}\n"
        f"Experience required: {job.experience_required or 'not specified'}\n"
        f"Required/mentioned skills: {_joined(job.extracted_skills, empty='(none extracted)')}\n"
        f"Full description:\n{job.description}\n\n"
        "CANDIDATE PROFILE\n"
        f"Title: {profile.title}\n"
        f"Summary: {profile.summary or '(none)'}\n"
        f"Skills: {_joined(profile.skills, empty='(none listed)')}\n"
        "Experience: "
        f"{profile.experience_years if profile.experience_years is not None else 'unspecified'} years\n"
        f"Seniority: {profile.seniority or 'unspecified'}\n"
        f"Education:\n{education_lines}\n"
        f"Certifications: {_joined(profile.certifications, empty='(none)')}\n"
        f"Projects: {_joined(profile.projects, empty='(none)')}\n"
        f"Industries: {_joined(profile.industries, empty='(none listed)')}\n"
        f"Target roles: {_joined(profile.target_roles, empty='(none listed)')}\n\n"
        "CANDIDATE'S SEARCH PREFERENCES\n"
        f"Target roles: {_joined(preferences.target_roles, empty='(none stated)')}\n"
        f"Target locations: {_joined(preferences.target_locations, empty='(none stated)')}\n"
        f"Remote preference: {remote_preference}\n"
        f"Excluded companies: {_joined(preferences.excluded_companies, empty='(none)')}\n\n"
        "Score each factor from 0.0 (no fit) to 1.0 (excellent fit):\n"
        "- role_relevance: how well the job's actual responsibilities match "
        "the profile's title/summary/target_roles\n"
        "- skills_fit: overlap and relevance between the job's "
        "skills/requirements and the profile's skills\n"
        "- experience_fit: whether the profile's years of experience and "
        "project history satisfy what the job asks for\n"
        "- domain_fit: alignment between the profile's industries and the "
        "job's domain/company context\n"
        "- seniority_fit: whether the profile's seniority matches the level "
        "the job is written for\n"
        "- location_preference_fit: whether the job's location/remote setup "
        "fits the candidate's stated location/remote preferences (use 0.5 "
        "if neutral/unspecified on either side, rather than penalizing)\n"
        "overall_score should be your holistic judgement (not simply an "
        "average of the factors above) of whether this candidate should be "
        "matched to this job. Also list matched_skills (skills the "
        "candidate has that the job wants) and missing_skills (skills the "
        "job wants that the candidate's profile does not show), and a short "
        "reasoning string."
    )


def to_profile_match_score(
    profile: ResumeProfile, output: ProfileScoringOutput
) -> ProfileMatchScore:
    return ProfileMatchScore(
        profile_id=profile.profile_id,
        resume_id=profile.resume_id,
        score=max(0.0, min(1.0, output.overall_score)),
        matched_skills=output.matched_skills,
        missing_skills=output.missing_skills,
    )


def score_profile_with_llm(
    llm_client: LLMClient,
    job: NormalizedJob,
    profile: ResumeProfile,
    preferences: UserPreferences,
) -> ProfileMatchScore:
    """Run one structured LLM call for `(job, profile)` and project the
    result into the canonical `ProfileMatchScore`. Raises
    `infrastructure.llm.LLMProviderError` on failure — callers (see
    `workflows.langgraph.job_matching.nodes.score_profile`) are responsible
    for the documented per-profile `score=0.0`-and-continue fallback.
    """
    output = llm_client.complete_structured(
        build_scoring_prompt(job, profile, preferences),
        ProfileScoringOutput,
        system=_SYSTEM_PROMPT,
    )
    return to_profile_match_score(profile, output)


__all__ = [
    "ProfileScoringOutput",
    "build_scoring_prompt",
    "score_profile_with_llm",
    "to_profile_match_score",
]
