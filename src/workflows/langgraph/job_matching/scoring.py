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
        "You are evaluating how well a CANDIDATE PROFILE fits a JOB POSTING.\n"
        "This platform serves every profession, so judge fit only from the "
        "information provided. Do not assume software engineering, technology, "
        "or any other profession from the title alone.\n\n"

        "IMPORTANT MATCHING PRINCIPLES\n"
        "Evaluate requirements semantically, not by exact keyword matching.\n\n"

        "For every requirement mentioned in the job posting, determine whether it is:\n"
        "1. mandatory/required,\n"
        "2. preferred/nice-to-have,\n"
        "3. an alternative requirement where one of several options is acceptable,\n"
        "4. a general capability that may be satisfied by a specific skill/tool,\n"
        "5. or a requirement where related experience is transferable but does "
        "not fully satisfy the requirement.\n\n"

        "Do NOT treat every technology, tool, qualification, or skill mentioned "
        "in the posting as independently mandatory.\n\n"

        "ALTERNATIVE REQUIREMENTS\n"
        "Pay close attention to wording such as:\n"
        "- X or Y\n"
        "- X/Y\n"
        "- X, Y, or Z\n"
        "- such as X, Y, Z\n"
        "- one of X/Y/Z\n"
        "- equivalent experience\n"
        "- comparable tools/frameworks/platforms\n\n"

        "If one acceptable alternative is satisfied, the other alternatives "
        "must NOT be listed as missing skills.\n\n"

        "Examples:\n"
        "- Job asks for 'React or Vue' and candidate has React -> requirement "
        "is satisfied; Vue is NOT a missing skill.\n"
        "- Job asks for 'React/Vue/Angular' and candidate has React -> requirement "
        "is satisfied.\n"
        "- Job asks for 'modern frontend framework such as React or Vue' and "
        "candidate has React -> requirement is satisfied.\n"
        "- Job asks for 'AWS or GCP' and candidate has AWS -> requirement is "
        "satisfied; GCP is NOT missing.\n"
        "- Job asks for 'SQL database experience' and candidate has MySQL -> "
        "requirement is satisfied.\n"
        "- Job asks for 'MySQL or PostgreSQL' and candidate has MySQL -> "
        "PostgreSQL is NOT missing.\n"
        "- Job says 'Vue preferred' and candidate has React -> React is related "
        "and transferable; Vue may be a minor gap but should have low impact.\n"
        "- Job says 'Vue required' and candidate only has React -> Vue remains "
        "a genuine gap, although React should be recognized as transferable "
        "frontend-framework experience.\n\n"

        "TRANSFERABLE SKILLS\n"
        "Recognize closely related experience when appropriate, but do not assume "
        "two technologies are identical merely because they belong to the same category.\n"
        "Use the wording of the job posting to decide whether related experience "
        "fully satisfies, partially satisfies, or does not satisfy a requirement.\n\n"

        "MANDATORY VS PREFERRED\n"
        "Mandatory requirements should have more impact on the score than "
        "preferred/nice-to-have requirements.\n"
        "Missing a preferred technology should not heavily penalize an otherwise "
        "strong candidate.\n\n"

        "GAP RULES\n"
        "Only include an item in missing_skills if the job genuinely requires or "
        "prefers a capability that the candidate does not adequately demonstrate.\n\n"

        "Do NOT include in missing_skills:\n"
        "- a technology that is only an unused alternative to a satisfied requirement,\n"
        "- a skill already present in matched_skills,\n"
        "- duplicate skills,\n"
        "- duplicate variants such as 'Git', 'Git (explicit)', 'Git version control',\n"
        "- company names,\n"
        "- job titles,\n"
        "- section headings,\n"
        "- skills merely mentioned in examples but not actually required/preferred.\n\n"

        "Before returning the result, verify:\n"
        "1. No skill appears in both matched_skills and missing_skills.\n"
        "2. missing_skills contains no duplicates or wording variants of the same skill.\n"
        "3. Alternative requirement groups are counted only once.\n"
        "4. Satisfying one valid alternative satisfies the whole alternative group.\n"
        "5. Preferred requirements have less impact than mandatory requirements.\n"
        "6. Transferable experience is acknowledged appropriately.\n"
        "7. The overall score reflects actual job fit, not raw keyword overlap.\n\n"

        "JOB POSTING\n"
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location or 'not specified'}\n"
        f"Experience required: {job.experience_required or 'not specified'}\n"
        f"Required/mentioned skills: "
        f"{_joined(job.extracted_skills, empty='(none extracted)')}\n"
        f"Full description:\n{job.description}\n\n"

        "CANDIDATE PROFILE\n"
        f"Title: {profile.title}\n"
        f"Summary: {profile.summary or '(none)'}\n"
        f"Skills: {_joined(profile.skills, empty='(none listed)')}\n"
        "Experience: "
        f"{profile.experience_years if profile.experience_years is not None else 'unspecified'} "
        "years\n"
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
        f"Excluded companies: "
        f"{_joined(preferences.excluded_companies, empty='(none)')}\n\n"

        "SCORING\n"
        "Score each factor from 0.0 (no fit) to 1.0 (excellent fit).\n\n"

        "- role_relevance: how well the actual responsibilities and purpose of "
        "the role align with the candidate's title, summary, projects, and target roles.\n"

        "- skills_fit: how well the candidate satisfies the job's actual skill "
        "requirements after considering mandatory vs preferred requirements, "
        "alternatives, general capability requirements, and transferable skills. "
        "Do NOT calculate this as simple keyword overlap.\n"

        "- experience_fit: whether the candidate's years of experience, work "
        "history, and projects reasonably satisfy the stated experience requirements. "
        "Do not invent experience requirements when the job does not specify them.\n"

        "- domain_fit: alignment between the candidate's industries/domain "
        "experience and the actual domain/context of the job.\n"

        "- seniority_fit: whether the candidate's demonstrated seniority aligns "
        "with the level and responsibilities described by the job.\n"

        "- location_preference_fit: whether the job location/remote arrangement "
        "fits the candidate's preferences. Use 0.5 when the job or preference is "
        "neutral/unspecified instead of penalizing the candidate.\n\n"

        "OVERALL SCORE\n"
        "overall_score must be a holistic assessment of whether this candidate "
        "is a realistic fit for the role.\n"
        "Do NOT simply average the factor scores.\n"
        "Do NOT calculate it from the percentage of exact skill keywords matched.\n"
        "A candidate with strong transferable experience and one satisfied option "
        "from an alternative requirement should not be heavily penalized for not "
        "having every technology mentioned in that alternative group.\n\n"

        "OUTPUT CONTENT\n"
        "Also return:\n"
        "- matched_skills: actual job requirements/capabilities that the candidate "
        "satisfies, including clearly transferable equivalents where appropriate.\n"
        "- missing_skills: genuine unsatisfied required/preferred capabilities only.\n"
        "- reasoning: a concise explanation of the strongest fit signals, important "
        "gaps, and how alternative/transferable requirements affected the judgement.\n\n"

        "FINAL SELF-CHECK\n"
        "Before answering, inspect matched_skills and missing_skills one final time:\n"
        "- remove duplicates,\n"
        "- remove semantic duplicates,\n"
        "- remove any gap already satisfied by an accepted alternative,\n"
        "- remove any item appearing in both lists,\n"
        "- ensure the overall score is consistent with the reasoning."
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
