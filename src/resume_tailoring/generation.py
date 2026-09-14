"""LLM call #1 of resume tailoring: rewrite one profile's *presentation*
for one specific job.

`TailoredProfileContent` below is an internal LLM-call contract, not a
shared type — only `ResumeProfile` (`shared.types.dto`) leaves this
component, via `apply_tailored_content()`. This mirrors
`workflows.langgraph.job_matching.scoring.ProfileScoringOutput`, which
defines its own local structured-output schema for exactly the same reason.

Three properties of this module are product requirements, not
implementation details:

**Only the summary and each job's bullets are tailored.** Everything else a
resume shows — contact details, links, skills, education, certifications,
projects, and every employer/title/date in the work history — is the
candidate's own extracted data and renders exactly as recorded, identically
for every user. Those fields are not in the schema below at all, so a
tailoring pass is not merely instructed to leave them alone: it is never
given the chance to touch them. `document.build_resume_document` then
renders one shared visual template over that per-user data.

**Anti-fabrication.** docs/tailored-resume/PRODUCT.md's "LLM Hallucination
in Work History" risk requires the tailoring prompt be "constrained to
rephrase/reorder/emphasize existing content ... not invent new employers,
titles, or achievements". That constraint is enforced twice over: the
prompt states it explicitly, scoped to the two things the model does write
(see `_ANTI_FABRICATION_RULES`), and the narrowed schema plus
`apply_tailored_content` make every other field structurally unreachable. A
prompt instruction the model can ignore is not a mitigation on its own.

**Profession independence.** Nothing here branches on a title, industry, or
skill (about_project.md; CLAUDE.md rules 3-4). The same prompt tailors a
nurse's resume, a machinist's, and a backend engineer's.
"""

from __future__ import annotations

from pydantic import BaseModel

from infrastructure.llm import LLMClient, append_custom_instructions
from infrastructure.logging import format_context, get_logger
from resume_tailoring.errors import TailoringError
from shared.errors.codes import ErrorCode
from shared.types.dto import (
    EducationEntry,
    NormalizedJob,
    ResumeProfile,
    WorkExperienceEntry,
)

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are an expert, profession-agnostic resume editor. You re-present a "
    "candidate's EXISTING experience so a specific job's reviewer sees the "
    "relevant parts first. You work for any profession (skilled trades, "
    "healthcare, teaching, finance, logistics, engineering, design, "
    "students/freshers, and more) and never assume the profession from a job "
    "title alone. You are an editor, not an author: every fact in your output "
    "must already be present in the candidate's profile."
)

# Kept as a module constant so the anti-fabrication contract is one
# reviewable block of text rather than something spread through an f-string,
# and so a test can assert on it directly (tests/resume_tailoring/
# test_generation.py) the way tests/profiles/test_parsing.py asserts on the
# extraction prompt's equivalent rules.
_ANTI_FABRICATION_RULES = """\
ABSOLUTE RULES — READ BEFORE WRITING ANYTHING

You are rewriting exactly two things: the professional summary, and the
bullet points under each position the candidate already held. Nothing else
about this resume changes. The contact details, the skills list, education,
certifications, projects, and every employer, position title and date in
the work history come straight from the candidate's own records — they are
not yours to write, and you are not asked for them.

Within those two things you may ONLY rephrase, reorder, and re-emphasize
content that is already in the candidate profile below. You are editing
presentation, never facts.

- NEVER invent an employer, position title, skill, credential,
  qualification, certification, degree, date, metric, or achievement that
  the original profile does not already state or directly imply.
- NEVER claim, in the summary or in any bullet, a skill the candidate does
  not already have, however loudly this posting asks for it. Leaving a
  genuine gap visible is correct behaviour; closing it with a skill they do
  not have is a fabrication.
- NEVER invent a bullet point. Every bullet you write for a position must
  correspond to an original bullet of THAT SAME position, rewritten. You
  may drop a bullet that is irrelevant to this job, and you may reorder the
  bullets within a position so the relevant ones come first.
- NEVER move an achievement, responsibility or bullet from one position to
  another, and never merge two positions' work into one list. Each list you
  return describes only what the profile already records under that one
  position.
- NEVER inflate seniority, scope, team size, budget, or years of
  experience, and never restate a number the original does not state.
- NEVER restate, correct, complete or refer to the candidate's contact
  details.

GOOD PRACTICE FOR THE TWO THINGS YOU DO WRITE

- Where the job posting's own wording accurately and truthfully describes
  something already in the profile, you may borrow the posting's term
  instead of the profile's original phrasing for clarity — this only ever
  relabels the SAME underlying capability, never a larger or different one.
  If the profile shows a related or adjacent capability rather than the
  exact one the posting names, describe it in the candidate's own words and
  never relabel it with the posting's exact term.
- Avoid vague filler such as "worked on," "responsible for," "helped with,"
  or "involved in" — state what was done, with what, and toward what
  outcome, using only details already present in the profile.
- Reuse a number exactly as the profile states it. Never round it toward a
  more impressive-sounding figure, and never add a number the profile does
  not already give.

If you cannot make the summary or a position's bullets more relevant
without breaking one of these rules, return them unchanged. An honest,
unchanged answer is a correct answer.
"""


class TailoredProfileContent(BaseModel):
    """The structured shape requested of the LLM for one (job, profile) pair.

    Two fields, and deliberately only two. A tailored resume differs from
    the original in what the candidate *says about* their experience — the
    summary, and how each position's bullets are worded and ordered. It must
    not differ in the facts: contact details, links, skills, education,
    certifications, projects, and every employer/title/date are the
    candidate's real, extracted data and render identically in the tailored
    document. None of them appear here, so the model is never even invited
    to rewrite them, and no amount of prompt drift can make it possible.

    `bullets_by_job` is positional: entry *i* is the rewritten bullet list
    for `profile.work_history[i]`, and the list must be exactly as long as
    the work history. The prompt states the required count explicitly and
    `apply_tailored_content` rejects a response that gets it wrong rather
    than risk attaching one employer's achievements to another.

    The identity fields (`profile_id`/`resume_id`/`user_id`/`status`) are
    not asked for either — a tailored resume is a hypothetical variant of
    the same underlying resume, so those are carried over by
    `apply_tailored_content`, never generated.
    """

    summary: str
    bullets_by_job: list[list[str]] = []


def _joined(values: list[str], *, empty: str) -> str:
    return ", ".join(values) if values else empty


def _render_work_history(entries: list[WorkExperienceEntry]) -> str:
    if not entries:
        return "(none recorded)"
    blocks: list[str] = []
    for index, entry in enumerate(entries, start=1):
        dates = f"{entry.start_date or 'unspecified'} - {entry.end_date or 'Present'}"
        bullets = (
            "\n".join(f"    - {bullet}" for bullet in entry.bullets)
            or "    (no bullet points recorded)"
        )
        blocks.append(
            f"  [{index}] {entry.title} — {entry.company} ({dates})\n{bullets}"
        )
    return "\n".join(blocks)


def _render_education(entries: list[EducationEntry]) -> str:
    if not entries:
        return "(none listed)"
    return "\n".join(
        f"  - {entry.degree or 'Degree'} in "
        f"{entry.field_of_study or 'unspecified field'} — {entry.institution}"
        for entry in entries
    )


def build_tailoring_prompt(
    job: NormalizedJob,
    profile: ResumeProfile,
    *,
    matched_skills: list[str],
    missing_skills: list[str],
    custom_instructions: str | None = None,
) -> str:
    """Render LLM call #1's prompt.

    `matched_skills`/`missing_skills` come from the job's already-persisted
    `JobMatch` row — the same analysis that produced the base score the
    tailored version is measured against. Passing them in is what makes this
    a *targeted* rewrite rather than a generic polish: the model is told
    which strengths the matcher already credited (lead with these) and which
    gaps it flagged (address honestly from existing evidence, or leave
    alone) instead of guessing at both from the job text.

    `custom_instructions` is the user's own saved preference for this skill
    (docs/prompt-customization/PRODUCT.md), loaded by
    `infrastructure.llm.get_skill_customization`. It is appended — quoted
    and explicitly subordinated — at exactly one place: the very end,
    *after* `_ANTI_FABRICATION_RULES` and after FINAL SELF-CHECK, by the
    single `append_custom_instructions` call wrapping the return below.
    There is no second site in this function where it could be placed
    earlier, and `None` (the case for every user who has not opted in)
    returns the prompt byte-for-byte unchanged. See
    `infrastructure/llm/user_prompts.py` for why that ordering is a safety
    boundary and not a style preference.
    """
    return append_custom_instructions(
        "Re-present a CANDIDATE PROFILE for one specific JOB POSTING.\n"
        "This platform serves every profession. Judge relevance only from "
        "the information provided; do not assume software engineering, "
        "technology, or any other profession from the job title alone.\n\n"

        f"{_ANTI_FABRICATION_RULES}\n"

        "JOB POSTING\n"
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location or 'not specified'}\n"
        f"Experience required: {job.experience_required or 'not specified'}\n"
        "Required/mentioned skills: "
        f"{_joined(job.extracted_skills, empty='(none extracted)')}\n"
        f"Full description:\n{job.description}\n\n"

        "PRIOR MATCH ANALYSIS FOR THIS EXACT JOB\n"
        "This candidate has already been scored against this posting. The "
        "analysis found:\n"
        "- Requirements the candidate ALREADY SATISFIES (matched skills): "
        f"{_joined(matched_skills, empty='(none identified)')}\n"
        "- Requirements the candidate DOES NOT currently satisfy (missing "
        f"skills): {_joined(missing_skills, empty='(none identified)')}\n\n"
        "Use this analysis to decide what to lead with:\n"
        "- Matched skills are proven strengths for this role. Lead the "
        "summary with them and make the bullets that evidence them "
        "prominent, concrete and first within their position.\n"
        "- Missing skills are genuine gaps. Do NOT claim them in the summary "
        "and do NOT claim them in a bullet. Only where the existing profile "
        "already contains closely related, genuinely transferable "
        "experience, surface that experience in the candidate's own terms so "
        "a reviewer can judge it — and even then, never describe it as the "
        "missing skill itself.\n\n"

        "CANDIDATE PROFILE (the ONLY source of facts you may use)\n"
        f"Title: {profile.title}\n"
        f"Summary: {profile.summary or '(none)'}\n"
        f"Skills: {_joined(profile.skills, empty='(none listed)')}\n"
        "Experience: "
        f"{profile.experience_years if profile.experience_years is not None else 'unspecified'} "
        "years\n"
        f"Seniority: {profile.seniority or 'unspecified'}\n"
        f"Work history:\n{_render_work_history(profile.work_history)}\n"
        f"Education:\n{_render_education(profile.education)}\n"
        f"Certifications: {_joined(profile.certifications, empty='(none)')}\n"
        f"Projects: {_joined(profile.projects, empty='(none)')}\n"
        f"Industries: {_joined(profile.industries, empty='(none listed)')}\n\n"

        "WHAT TO RETURN\n"
        "Return exactly two fields, and nothing else. The skills list, "
        "education, certifications, projects, contact details and every "
        "employer, position title and date are taken from the candidate's "
        "own records and are not part of your answer.\n"
        "- summary: 2-4 sentences leading with the strongest genuine fit "
        "signals for THIS job, drawn only from the profile above.\n"
        f"- bullets_by_job: EXACTLY {len(profile.work_history)} list(s) of "
        "bullet strings — one list per position in the work history above, "
        "in the SAME order. The first list belongs to position [1], the "
        "second to position [2], and so on. Rewrite each position's own "
        "bullets to foreground what matters for this job, keeping them "
        "concrete and in the candidate's own register. A position whose "
        "bullets already read well for this job keeps them as they are — "
        "return them anyway. Never return more or fewer lists than there "
        "are positions, and never omit a position's list to skip over it: "
        "an omitted list would silently attach the next position's work to "
        "the wrong employer.\n\n"

        "FINAL SELF-CHECK\n"
        "Before answering, verify against the ABSOLUTE RULES:\n"
        f"1. bullets_by_job has exactly {len(profile.work_history)} list(s), "
        "one per position, in the work history's own order.\n"
        "2. Every bullet traces back to an original bullet of that same "
        "position — none has moved between positions.\n"
        "3. No missing skill has been claimed as possessed, in the summary "
        "or in any bullet.\n"
        "4. No number, duration, scope or seniority claim has been added or "
        "increased.\n"
        "5. You have returned only the summary and the bullet lists.",
        # The append point. Everything above is the hardened prompt; this
        # is the only thing the user contributes to it, and it lands after
        # all of it.
        custom_instructions,
    )


def generate_tailored_content(
    llm_client: LLMClient,
    job: NormalizedJob,
    profile: ResumeProfile,
    *,
    matched_skills: list[str],
    missing_skills: list[str],
) -> TailoredProfileContent:
    """Run LLM call #1 and return the validated tailored content.

    Raises `infrastructure.llm.LLMProviderError` on failure — the caller
    (`resume_tailoring.service.ResumeTailoringService.tailor_resume`)
    normalizes it into a `TailoringError`.
    """
    return llm_client.complete_structured(
        build_tailoring_prompt(
            job,
            profile,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
        ),
        TailoredProfileContent,
        system=_SYSTEM_PROMPT,
    )


def _tailored_bullets(
    entry: WorkExperienceEntry, bullets: list[str]
) -> WorkExperienceEntry:
    """One work-history entry with only its `bullets` replaced.

    `company`, `title`, `start_date` and `end_date` are carried on the copy
    untouched — they are facts about the candidate's employment, and the
    model was never asked for them.

    An empty list back for a position is read as a dropped section rather
    than a tailoring decision, exactly as the previous whole-section
    fallback did: a job entry with no bullets at all is never what the user
    asked for and would score worse than the original, so the position keeps
    its own bullets.
    """
    cleaned = [bullet.strip() for bullet in bullets if bullet and bullet.strip()]
    if not cleaned:
        return entry
    return entry.model_copy(update={"bullets": cleaned})


def apply_tailored_content(
    profile: ResumeProfile, content: TailoredProfileContent
) -> ResumeProfile:
    """Wrap LLM call #1's output back into a real `ResumeProfile`.

    The tailored profile is the ORIGINAL profile with two substitutions:
    `summary`, and each `work_history[i].bullets`. Everything else — every
    id, `title`, `contact` (name, email, phone, location, links),
    `skills`, `experience_years`, `seniority`, `education`,
    `certifications`, `projects`, `industries`, `target_roles`, `status`,
    and each entry's `company`/`title`/`start_date`/`end_date` — is
    identical to what came out of the database, because it is literally the
    same object copied forward. That is what makes the rendered document one
    shared template over the candidate's own real data rather than something
    an LLM re-authored.

    Keeping the ids also means `score_profile_with_llm` projects the
    tailored score onto the same profile/resume the base score was computed
    for, which is what makes the two numbers comparable.

    Raises `TailoringError(LLM_PROVIDER_ERROR)` when `bullets_by_job` is not
    exactly as long as `work_history`. Index *i* of that list is position
    *i*'s bullets and nothing else identifies which position a list belongs
    to, so a wrong-length response has no safe interpretation: padding it
    would leave the tail untailored while implying it was considered, and
    truncating it would attach one employer's achievements to another if the
    model had dropped a position from the middle. A resume that credits the
    wrong employer with your work is worse than no tailored resume at all,
    so this fails loudly — the route surfaces it as an LLM error and the
    user can retry.
    """
    expected = len(profile.work_history)
    returned = len(content.bullets_by_job)
    if returned != expected:
        logger.error(
            "Tailoring returned the wrong number of bullet lists | %s",
            format_context(
                profile_id=profile.profile_id,
                user_id=profile.user_id,
                expected_jobs=expected,
                returned_lists=returned,
            ),
        )
        raise TailoringError(
            ErrorCode.LLM_PROVIDER_ERROR,
            "the tailored content did not line up with the candidate's work "
            f"history ({returned} bullet list(s) returned for {expected} "
            "position(s)), so it could not be applied without risking "
            "attributing one employer's work to another",
        )

    return profile.model_copy(
        update={
            "summary": content.summary.strip() or profile.summary,
            "work_history": [
                _tailored_bullets(entry, bullets)
                for entry, bullets in zip(
                    profile.work_history, content.bullets_by_job, strict=True
                )
            ],
        }
    )


__all__ = [
    "TailoredProfileContent",
    "apply_tailored_content",
    "build_tailoring_prompt",
    "generate_tailored_content",
]
