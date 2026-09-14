"""LLM call #1: the tailoring prompt and how its output is wrapped back
into a `ResumeProfile`.

The prompt assertions here are the same kind as
`tests/profiles/test_parsing.py`'s assertions on the extraction prompt: the
anti-fabrication constraint is a product requirement
(docs/tailored-resume/PRODUCT.md, "LLM Hallucination in Work History"), so
its presence is tested, not assumed. `apply_tailored_content`'s tests cover
the half of that constraint a prompt cannot enforce — the fields the model
is structurally not allowed to change.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from profiles.repository import to_resume_profile
from resume_tailoring.errors import TailoringError
from resume_tailoring.generation import (
    TailoredProfileContent,
    apply_tailored_content,
    build_tailoring_prompt,
)
from resume_tailoring.service import to_normalized_job
from shared.errors.codes import ErrorCode
from shared.types.dto import ContactInfo, WorkExperienceEntry
from shared.types.ids import UserId
from tests.resume_tailoring.conftest import (
    make_candidate_profile,
    make_job,
    make_job_match,
)


def _prompt_fixture() -> tuple[str, object, object]:
    user_id = UserId(uuid4())
    job = make_job(user_id)
    profile = make_candidate_profile(user_id)
    job_match = make_job_match(user_id, job.id, profile)
    prompt = build_tailoring_prompt(
        to_normalized_job(job),
        to_resume_profile(profile),
        matched_skills=job_match.matched_skills,
        missing_skills=job_match.missing_skills,
    )
    return prompt, job_match, profile


# ---------------------------------------------------------------------------
# Anti-fabrication
# ---------------------------------------------------------------------------


def test_prompt_forbids_inventing_content() -> None:
    """PRODUCT.md's mitigation for LLM hallucination is that the tailoring
    prompt is "constrained to rephrase/reorder/emphasize existing content
    ... not invent new employers, titles, or achievements". If this
    instruction is ever dropped or softened, the feature silently starts
    producing resumes a user could be caught out on in an interview.
    """
    prompt, _, _ = _prompt_fixture()

    assert (
        "NEVER invent an employer, position title, skill, credential,\n"
        "  qualification, certification, degree, date, metric, or achievement "
        "that\n  the original profile does not already state or directly imply."
    ) in prompt


def test_prompt_forbids_adding_a_skill_the_candidate_lacks() -> None:
    """The most tempting fabrication for a *tailoring* prompt specifically:
    the model has just been handed a list of missing skills, and "closing"
    a gap by claiming it would raise the score while making the resume a
    lie. It can no longer touch the skills list at all, so the rule is now
    about the two things it does write.
    """
    prompt, _, _ = _prompt_fixture()

    assert (
        "NEVER claim, in the summary or in any bullet, a skill the candidate "
        "does\n  not already have"
    ) in prompt
    assert "Do NOT claim them in the summary " in prompt


def test_prompt_forbids_altering_employment_facts() -> None:
    prompt, _, _ = _prompt_fixture()

    assert "NEVER invent a bullet point" in prompt
    assert "NEVER inflate seniority, scope, team size, budget, or years of" in prompt
    assert (
        "NEVER move an achievement, responsibility or bullet from one position to"
    ) in prompt


def test_prompt_states_that_only_the_summary_and_bullets_change() -> None:
    """The user-facing promise behind the narrowed schema: the facts on a
    tailored resume are the candidate's own extracted data, rendered the
    same way for everyone. The schema already makes that structural — the
    prompt says it too so the model does not waste effort trying.
    """
    prompt, _, _ = _prompt_fixture()

    assert (
        "You are rewriting exactly two things: the professional summary, and "
        "the\nbullet points under each position the candidate already held."
    ) in prompt
    assert "Return exactly two fields, and nothing else." in prompt


def test_prompt_states_the_exact_number_of_bullet_lists_expected() -> None:
    """`bullets_by_job` is matched to the work history by position and
    nothing else, so the count is the one thing the model must not get
    wrong — `apply_tailored_content` refuses the response outright if it
    does.
    """
    _, _, profile = _prompt_fixture()
    prompt = build_tailoring_prompt(
        to_normalized_job(make_job(profile.user_id)),
        to_resume_profile(profile),
        matched_skills=[],
        missing_skills=[],
    )

    assert len(profile.work_history) == 1, "fixture assumption"
    assert "EXACTLY 1 list(s) of bullet strings" in prompt
    assert "bullets_by_job has exactly 1 list(s)" in prompt


# ---------------------------------------------------------------------------
# The prompt is informed by the real JobMatch analysis
# ---------------------------------------------------------------------------


def test_prompt_includes_the_job_matchs_missing_skills() -> None:
    """Tailoring must be driven by what the matcher actually found missing
    for THIS job, not by a generic re-read of the posting — that is the
    whole reason the persisted `JobMatch` is loaded first.
    """
    prompt, job_match, _ = _prompt_fixture()

    assert job_match.missing_skills, "fixture must supply missing skills to assert on"
    for skill in job_match.missing_skills:
        assert skill in prompt


def test_prompt_includes_the_job_matchs_matched_skills() -> None:
    prompt, job_match, _ = _prompt_fixture()

    assert job_match.matched_skills, "fixture must supply matched skills to assert on"
    for skill in job_match.matched_skills:
        assert skill in prompt


def test_prompt_labels_matched_and_missing_skills_distinctly() -> None:
    """Both lists reaching the prompt is not enough — a model that cannot
    tell which list is which would treat a gap as a strength.
    """
    prompt, job_match, _ = _prompt_fixture()

    satisfied_line = next(
        line for line in prompt.splitlines() if "ALREADY SATISFIES" in line
    )
    missing_line = next(
        line for line in prompt.splitlines() if "DOES NOT currently satisfy" in line
    )

    assert job_match.matched_skills[0] in satisfied_line
    assert job_match.matched_skills[0] not in missing_line
    assert job_match.missing_skills[0] not in satisfied_line


def test_prompt_includes_the_job_and_the_profiles_own_content() -> None:
    prompt, _, profile = _prompt_fixture()
    job = make_job(profile.user_id)

    assert profile.title in prompt
    assert profile.work_history[0].company in prompt
    assert profile.work_history[0].bullets[0] in prompt
    assert profile.education[0].institution in prompt
    assert job.title in prompt


def test_prompt_does_not_assume_a_single_profession() -> None:
    """about_project.md; CLAUDE.md rules 3-4 — the platform must stay
    profession-independent, so the prompt says so explicitly rather than
    leaving the model to infer a profession from the job title, exactly as
    the extraction and scoring prompts already do.
    """
    prompt, _, _ = _prompt_fixture()
    lowered = prompt.lower()

    assert "every profession" in lowered
    assert "do not assume software engineering" in lowered


def test_prompt_allows_truthful_terminology_alignment_but_not_relabeling() -> None:
    """Borrowing the posting's own wording for an already-true capability is
    allowed (clarity), but relabeling an adjacent/transferable capability
    with the posting's exact term is not (that would be a fabrication by
    another name).
    """
    prompt, _, _ = _prompt_fixture()

    assert "you may borrow the posting's term" in prompt
    assert "never relabel it with the posting's exact term" in prompt


def test_prompt_discourages_vague_filler_language() -> None:
    prompt, _, _ = _prompt_fixture()

    assert '"worked on," "responsible for," "helped with,"' in prompt


def test_prompt_forbids_rounding_or_adding_numbers() -> None:
    prompt, _, _ = _prompt_fixture()

    assert "Reuse a number exactly as the profile states it." in prompt
    assert "never add a number the profile does\n  not already give." in prompt


def test_prompt_carries_no_profession_specific_instructions() -> None:
    """The *instructions* must be the same for every profession — the only
    field-specific words in the rendered prompt may come from the job and
    profile being tailored, never from the instruction text itself. The
    fixture is a nurse, so any hard-coded technology vocabulary would have
    to come from the template.
    """
    from resume_tailoring.generation import _ANTI_FABRICATION_RULES

    instructions = _ANTI_FABRICATION_RULES.lower()

    for word in ("software", "developer", "github", "tech stack", "nurse", "engineer"):
        assert word not in instructions


# ---------------------------------------------------------------------------
# apply_tailored_content — the structural half of the anti-fabrication rule
# ---------------------------------------------------------------------------


def _base_profile(**overrides):
    return to_resume_profile(make_candidate_profile(UserId(uuid4()), **overrides))


def _two_job_profile():
    return _base_profile(
        contact=ContactInfo(
            full_name="Aoife Devlin",
            email="aoife.devlin@example.com",
            phone="+44 7700 900123",
            location="Leeds, UK",
            linkedin_url="linkedin.com/in/aoife-devlin",
            links_checked=True,
        ),
        work_history=[
            WorkExperienceEntry(
                company="St Bede's Trust",
                title="Staff Nurse, High Dependency Unit",
                start_date="Aug 2019",
                end_date=None,
                bullets=["Cared for post-operative patients needing ventilation"],
            ),
            WorkExperienceEntry(
                company="Northwind Clinic",
                title="Staff Nurse",
                start_date="2017",
                end_date="2019",
                bullets=["Triage for a 20-bed ward"],
            ),
        ],
    )


def test_the_schema_asks_for_only_a_summary_and_bullets() -> None:
    """The structural half of the product rule. Skills, education,
    projects, contact and the work history's own facts are the candidate's
    real data — the model is not asked for them, so no prompt regression can
    make it possible for it to rewrite them.
    """
    assert set(TailoredProfileContent.model_fields) == {"summary", "bullets_by_job"}


def test_tailored_profile_keeps_the_original_identity_fields() -> None:
    """A tailored resume is a hypothetical variant of the same underlying
    resume, so it carries the same ids — which is also what makes the two
    scores comparable, since `score_profile_with_llm` projects its result
    onto them.
    """
    profile = _base_profile()

    tailored = apply_tailored_content(
        profile,
        TailoredProfileContent(summary="Rewritten.", bullets_by_job=[["A bullet"]]),
    )

    assert tailored.profile_id == profile.profile_id
    assert tailored.resume_id == profile.resume_id
    assert tailored.user_id == profile.user_id
    assert tailored.status == profile.status


def test_everything_except_the_summary_and_bullets_is_byte_identical() -> None:
    """The whole product rule in one assertion.

    A tailored resume is one shared visual template over the candidate's
    own real record: their contact details and links, their skills, their
    education, their projects — and, in the work history, the employers,
    titles and dates they actually held. Only the summary and each
    position's bullets are the model's work. Compared field by field
    against the original rather than spot-checked, so a field added to
    `ResumeProfile` later cannot quietly become tailorable.
    """
    profile = _two_job_profile()

    tailored = apply_tailored_content(
        profile,
        TailoredProfileContent(
            summary="A tailored summary.",
            bullets_by_job=[["Rewritten bullet one"], ["Rewritten bullet two"]],
        ),
    )

    untouched = set(type(profile).model_fields) - {"summary", "work_history"}
    for field in untouched:
        assert getattr(tailored, field) == getattr(profile, field), field

    assert tailored.contact == profile.contact
    assert tailored.contact is not None
    assert tailored.contact.linkedin_url == "linkedin.com/in/aoife-devlin"

    for tailored_entry, original_entry in zip(
        tailored.work_history, profile.work_history, strict=True
    ):
        assert tailored_entry.company == original_entry.company
        assert tailored_entry.title == original_entry.title
        assert tailored_entry.start_date == original_entry.start_date
        assert tailored_entry.end_date == original_entry.end_date

    assert tailored.summary == "A tailored summary."
    assert tailored.work_history[0].bullets == ["Rewritten bullet one"]
    assert tailored.work_history[1].bullets == ["Rewritten bullet two"]


def test_bullets_are_matched_to_their_own_job_by_position() -> None:
    """`bullets_by_job[i]` belongs to `work_history[i]` and to nothing else.
    Getting this wrong would credit one employer with another's work — the
    single worst thing this feature could do to a user in an interview.
    """
    profile = _two_job_profile()

    tailored = apply_tailored_content(
        profile,
        TailoredProfileContent(
            summary="s",
            bullets_by_job=[["Belongs to St Bede's"], ["Belongs to Northwind"]],
        ),
    )

    by_company = {entry.company: entry.bullets for entry in tailored.work_history}
    assert by_company["St Bede's Trust"] == ["Belongs to St Bede's"]
    assert by_company["Northwind Clinic"] == ["Belongs to Northwind"]


def test_too_few_bullet_lists_is_refused() -> None:
    """A response with fewer lists than positions has no safe reading: pad
    it and the tail is silently untailored, truncate it and a dropped
    middle position shifts every later list onto the wrong employer. So it
    is refused, loudly, rather than guessed at.
    """
    profile = _two_job_profile()

    with pytest.raises(TailoringError) as excinfo:
        apply_tailored_content(
            profile,
            TailoredProfileContent(summary="s", bullets_by_job=[["Only one list"]]),
        )

    assert excinfo.value.code is ErrorCode.LLM_PROVIDER_ERROR
    assert "1 bullet list(s) returned for 2 position(s)" in excinfo.value.message


def test_too_many_bullet_lists_is_refused() -> None:
    profile = _two_job_profile()

    with pytest.raises(TailoringError) as excinfo:
        apply_tailored_content(
            profile,
            TailoredProfileContent(
                summary="s", bullets_by_job=[["a"], ["b"], ["an invented third job"]]
            ),
        )

    assert excinfo.value.code is ErrorCode.LLM_PROVIDER_ERROR
    assert "3 bullet list(s) returned for 2 position(s)" in excinfo.value.message


def test_a_profile_with_no_work_history_expects_no_bullet_lists() -> None:
    """A resume that states no positions is legitimate on a platform
    serving every profession (a fresher, a career changer). The count rule
    holds trivially: zero lists for zero positions.
    """
    profile = _base_profile(work_history=[])

    tailored = apply_tailored_content(
        profile, TailoredProfileContent(summary="A tailored summary.", bullets_by_job=[])
    )

    assert tailored.work_history == []
    assert tailored.summary == "A tailored summary."


def test_an_empty_bullet_list_falls_back_to_that_jobs_own_bullets() -> None:
    """An empty list for a position is a dropped section, not a tailoring
    decision — a job entry with no bullets under it is never what the user
    asked for and scores worse than the original. Only that position falls
    back; the others keep their rewrites.
    """
    profile = _two_job_profile()

    tailored = apply_tailored_content(
        profile,
        TailoredProfileContent(summary="s", bullets_by_job=[["Rewritten"], []]),
    )

    assert tailored.work_history[0].bullets == ["Rewritten"]
    assert tailored.work_history[1].bullets == profile.work_history[1].bullets


def test_an_empty_summary_falls_back_to_the_original() -> None:
    profile = _base_profile()

    tailored = apply_tailored_content(
        profile, TailoredProfileContent(summary="   ", bullets_by_job=[["A bullet"]])
    )

    assert tailored.summary == profile.summary


def test_blank_bullets_are_dropped_rather_than_rendered() -> None:
    """An empty string would render as a naked bullet character on the
    document."""
    profile = _base_profile()

    tailored = apply_tailored_content(
        profile,
        TailoredProfileContent(
            summary="s", bullets_by_job=[["  Real bullet  ", "", "   "]]
        ),
    )

    assert tailored.work_history[0].bullets == ["Real bullet"]
