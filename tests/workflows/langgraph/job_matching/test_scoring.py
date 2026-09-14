"""The scoring prompt, and how its output is projected onto
`ProfileMatchScore`.

The prompt assertions here are the same kind as
`tests/resume_tailoring/test_generation.py`'s assertions on the tailoring
prompt: an instruction that exists to stop the model inventing something is
a product requirement, so its presence is tested rather than assumed. The
projection tests cover the half a prompt cannot enforce — what actually
survives `to_profile_match_score` and reaches the rest of the system.

`hard_blocker` and `insufficient_detail` are explanation-only fields. The
tests below pin that explicitly: adding either must not move `score`,
`matched_skills` or `missing_skills`.
"""

from __future__ import annotations

import re
from uuid import uuid4

from shared.types.dto import ProfileMatchScore
from shared.types.ids import UserId
from tests.matching.conftest import (
    make_normalized_job,
    make_resume_profile,
    make_user_preferences,
)
from workflows.langgraph.job_matching.scoring import (
    ProfileScoringOutput,
    build_scoring_prompt,
    to_profile_match_score,
)


def _prompt_fixture() -> str:
    user_id = UserId(uuid4())
    return build_scoring_prompt(
        make_normalized_job(user_id),
        make_resume_profile(user_id),
        make_user_preferences(user_id),
    )


def _output(**overrides: object) -> ProfileScoringOutput:
    fields: dict[str, object] = {
        "role_relevance": 0.8,
        "skills_fit": 0.9,
        "experience_fit": 0.7,
        "domain_fit": 0.8,
        "seniority_fit": 0.9,
        "location_preference_fit": 0.5,
        "overall_score": 0.82,
        "matched_skills": ["CAD", "DFM"],
        "missing_skills": ["FEA"],
        "reasoning": "Strong overlap on the core design responsibilities.",
    }
    fields.update(overrides)
    return ProfileScoringOutput(**fields)


# ---------------------------------------------------------------------------
# Hard blockers — the prompt half
# ---------------------------------------------------------------------------


def test_prompt_requires_a_hard_blocker_to_be_stated_not_inferred() -> None:
    """The failure mode this field invites is a model inventing a
    requirement the posting never made — telling a user they are ineligible
    for a job they could actually get. The two-part gate is what stops it.
    """
    prompt = _prompt_fixture()

    assert (
        "A hard blocker is a requirement that the posting itself states as "
        "non-negotiable AND that this candidate clearly fails."
    ) in prompt
    assert "NEVER invent a requirement the posting does not actually state." in prompt
    assert "The absence of a statement is not a requirement." in prompt


def test_prompt_names_the_requirement_categories_it_must_not_assume() -> None:
    """Naming the categories a model is most tempted to hallucinate is the
    point of the rule — a generic "don't invent things" is too weak to stop
    a confident guess about, say, work authorization.
    """
    prompt = _prompt_fixture()

    for category in (
        "legal work authorization",
        "citizenship or residency",
        "background vetting or clearance",
        "a licence, registration or certification",
        "a specific degree or academic qualification",
        "a mandatory physical location or on-site presence",
        "a mandatory minimum number of years",
    ):
        assert category in prompt, category


def test_prompt_treats_profile_silence_as_unknown_rather_than_failure() -> None:
    """The second half of the gate. Most profiles say nothing at all about
    clearances or visas; reading that silence as failure would flag a
    blocker on nearly every posting that mentions one.
    """
    prompt = _prompt_fixture()

    assert "NEVER treat silence in the profile as failure." in prompt
    assert "that is unknown, not failed" in prompt
    assert "hard_blocker stays null" in prompt


def test_prompt_forbids_promoting_an_ordinary_gap_into_a_hard_blocker() -> None:
    """Without this the field degenerates into "the worst missing skill",
    which is already covered by missing_skills and would make the UI warning
    meaningless through overuse.
    """
    prompt = _prompt_fixture()

    assert (
        "do NOT promote an ordinary preferred or nice-to-have requirement into a "
        "hard blocker"
    ) in prompt
    assert (
        "confirm hard_blocker is not merely the most important entry in "
        "missing_skills restated"
    ) in prompt


def test_prompt_requires_null_when_there_is_no_blocker() -> None:
    prompt = _prompt_fixture()

    assert "If there is no such requirement, hard_blocker MUST be null." in prompt


def test_prompt_keeps_a_hard_blocker_from_forcing_the_score_down() -> None:
    """The three new fields are additive explanation. If a blocker were also
    allowed to move the number, this change would have altered existing
    scoring behavior.
    """
    prompt = _prompt_fixture()

    assert "A hard blocker does not by itself force the score to zero." in prompt


# ---------------------------------------------------------------------------
# Insufficient detail — the prompt half
# ---------------------------------------------------------------------------


def test_prompt_defines_insufficient_detail_by_substance_not_empty_fields() -> None:
    """The obvious wrong reading is "some fields are blank", which would fire
    on most manually-pasted postings and train the user to ignore the note.
    """
    prompt = _prompt_fixture()

    assert (
        "Judge this on the substance of the description text, not on which "
        "structured fields happen to be empty."
    ) in prompt
    assert (
        "A posting that describes the responsibilities and requirements properly "
        "is sufficient even if location, experience or the extracted skill list "
        "are blank."
    ) in prompt


def test_prompt_gives_concrete_examples_of_a_too_thin_posting() -> None:
    prompt = _prompt_fixture()

    assert "a one-line forwarded note" in prompt
    assert "a short informal social post" in prompt


def test_prompt_forbids_flagging_merely_short_postings() -> None:
    prompt = _prompt_fixture()

    assert (
        "Do NOT set it merely because the posting is short, informally written, "
        "or omits a detail you would have liked."
    ) in prompt
    assert "Otherwise it MUST be false." in prompt


def test_prompt_still_requires_a_score_when_detail_is_insufficient() -> None:
    """The flag qualifies the score; it must not become an excuse to skip
    scoring, or a thin posting would silently drop out of the pipeline.
    """
    prompt = _prompt_fixture()

    assert "still return your best score and still fill the other fields" in prompt
    assert "it does not replace it." in prompt


# ---------------------------------------------------------------------------
# Reasoning — the prompt half
# ---------------------------------------------------------------------------


def test_prompt_asks_for_reasoning_written_for_the_candidate() -> None:
    """`reasoning` is now shown in the UI rather than discarded, so the
    prompt has to ask for something a person would actually want to read.
    """
    prompt = _prompt_fixture()

    assert (
        "Write it for the candidate to read: two to four plain sentences, no "
        "bullet points, no restating of the numeric factor scores."
    ) in prompt


def test_prompt_lists_all_three_judgement_fields_in_output_content() -> None:
    prompt = _prompt_fixture()

    assert "- hard_blocker: a stated non-negotiable requirement this candidate" in prompt
    assert "- insufficient_detail: whether the posting is too thin to score with" in prompt


def test_prompt_self_checks_both_new_judgement_fields() -> None:
    """scoring.py's existing convention is that every instruction block ends
    up re-verified in FINAL SELF-CHECK. The new fields join it rather than
    being stated once and left.
    """
    prompt = _prompt_fixture()

    assert "Then check the two judgement fields one final time:" in prompt
    assert "if you cannot do both, set it to null" in prompt
    assert "if not, set it to false." in prompt


# ---------------------------------------------------------------------------
# Profession neutrality
# ---------------------------------------------------------------------------


def test_new_rule_blocks_carry_no_profession_specific_instructions() -> None:
    """about_project.md; CLAUDE.md rules 3-4. The *instructions* must read
    the same for every profession — only the interpolated job and profile
    may carry field-specific words.

    Asserted on the two new rule constants rather than the whole rendered
    prompt: the pre-existing ALTERNATIVE REQUIREMENTS section names real
    tools in its worked examples on purpose, and the data blocks interpolate
    whatever profession the user works in. The constants are the text this
    change actually adds, so they are the text this change must keep neutral.
    """
    from workflows.langgraph.job_matching.scoring import (
        _HARD_BLOCKER_RULES,
        _INSUFFICIENT_DETAIL_RULES,
    )

    instructions = (_HARD_BLOCKER_RULES + _INSUFFICIENT_DETAIL_RULES).lower()

    # Matched at a word start so a profession word cannot hide inside an
    # ordinary one ("cad" in "academic"), while a prefix still catches its
    # inflections ("engineer" catching "engineering").
    for word in (
        "software",
        "developer",
        "github",
        "tech stack",
        "engineer",
        "nurse",
        "python",
        "cad",
        "recruiter",
        "medical",
        "finance",
    ):
        assert re.search(rf"\b{re.escape(word)}", instructions) is None, word


def test_hard_blocker_rules_name_no_single_country_or_jurisdiction() -> None:
    """A clearance/visa rule is the easiest place to accidentally hard-code
    one country's vocabulary into a platform meant to serve any user.
    """
    from workflows.langgraph.job_matching.scoring import _HARD_BLOCKER_RULES

    instructions = _HARD_BLOCKER_RULES.lower()

    for word in ("visa", "h-1b", "green card", "us", "u.s.", "eu", "uk", "india"):
        assert re.search(rf"\b{re.escape(word)}\b", instructions) is None, word


# ---------------------------------------------------------------------------
# to_profile_match_score — what actually survives the projection
# ---------------------------------------------------------------------------


def test_reasoning_is_carried_through_rather_than_dropped() -> None:
    """The whole point of this change. The LLM was already being asked for
    `reasoning`; `to_profile_match_score` used to discard it.
    """
    profile = make_resume_profile(UserId(uuid4()))

    score = to_profile_match_score(
        profile, _output(reasoning="Strong overlap on the core responsibilities.")
    )

    assert score.reasoning == "Strong overlap on the core responsibilities."


def test_reasoning_is_stripped() -> None:
    profile = make_resume_profile(UserId(uuid4()))

    score = to_profile_match_score(profile, _output(reasoning="  Padded.  \n"))

    assert score.reasoning == "Padded."


def test_a_hard_blocker_is_carried_through() -> None:
    profile = make_resume_profile(UserId(uuid4()))

    score = to_profile_match_score(
        profile,
        _output(hard_blocker="Posting requires an active licence the profile lacks."),
    )

    assert score.hard_blocker == "Posting requires an active licence the profile lacks."


def test_no_hard_blocker_stays_none() -> None:
    profile = make_resume_profile(UserId(uuid4()))

    assert to_profile_match_score(profile, _output()).hard_blocker is None


def test_a_blank_hard_blocker_is_normalized_to_none() -> None:
    """A structured-output model asked for `str | None` will often answer
    with an empty string instead of a real null. The UI raises a prominent
    warning on any non-None value, so an empty string would render an empty
    alert box.
    """
    profile = make_resume_profile(UserId(uuid4()))

    for raw in ("", "   ", "\n"):
        assert to_profile_match_score(profile, _output(hard_blocker=raw)).hard_blocker is None


def test_a_hard_blocker_spelled_out_as_absent_is_normalized_to_none() -> None:
    """Same failure, spelled in words — otherwise the user gets a red
    blocker warning whose text reads "None".
    """
    profile = make_resume_profile(UserId(uuid4()))

    for raw in ("None", "none", "N/A", "n/a", "null", "None.", " NO "):
        assert (
            to_profile_match_score(profile, _output(hard_blocker=raw)).hard_blocker is None
        ), raw


def test_a_real_hard_blocker_is_not_over_normalized() -> None:
    """The normalization must key on the whole answer, not on a substring —
    a genuine blocker can legitimately contain the word "no".
    """
    profile = make_resume_profile(UserId(uuid4()))

    score = to_profile_match_score(
        profile, _output(hard_blocker="Posting states no remote option; candidate is remote-only.")
    )

    assert score.hard_blocker == (
        "Posting states no remote option; candidate is remote-only."
    )


def test_insufficient_detail_is_carried_through() -> None:
    profile = make_resume_profile(UserId(uuid4()))

    assert to_profile_match_score(profile, _output(insufficient_detail=True)).insufficient_detail is True
    assert to_profile_match_score(profile, _output()).insufficient_detail is False


# ---------------------------------------------------------------------------
# The three fields are additive — existing behavior is untouched
# ---------------------------------------------------------------------------


def test_the_new_fields_do_not_change_the_score_or_the_skill_lists() -> None:
    """The explicit constraint on this change: `overall_score`,
    `matched_skills` and `missing_skills` must be projected exactly as they
    were before, whatever the new fields say. Compared against the same
    output with the new fields absent rather than spot-checked.
    """
    profile = make_resume_profile(UserId(uuid4()))

    without = to_profile_match_score(profile, _output())
    with_flags = to_profile_match_score(
        profile,
        _output(
            hard_blocker="A stated mandatory requirement the candidate fails.",
            insufficient_detail=True,
        ),
    )

    assert with_flags.score == without.score
    assert with_flags.matched_skills == without.matched_skills
    assert with_flags.missing_skills == without.missing_skills


def test_the_score_is_still_clamped_to_the_unit_interval() -> None:
    """Pre-existing behavior, re-pinned here because this file is now the
    home of `to_profile_match_score`'s tests.
    """
    profile = make_resume_profile(UserId(uuid4()))

    assert to_profile_match_score(profile, _output(overall_score=1.0)).score == 1.0
    assert to_profile_match_score(profile, _output(overall_score=0.0)).score == 0.0


def test_identity_fields_still_come_from_the_profile() -> None:
    profile = make_resume_profile(UserId(uuid4()))

    score = to_profile_match_score(profile, _output())

    assert score.profile_id == profile.profile_id
    assert score.resume_id == profile.resume_id


# ---------------------------------------------------------------------------
# Backward compatibility of the persisted shape
# ---------------------------------------------------------------------------


def test_a_profile_score_persisted_before_this_change_still_loads() -> None:
    """`ProfileMatchScore` is stored as JSON inside
    `job_matches.profile_scores`, so rows written before these fields
    existed simply lack the keys. The defaults are what make this change
    migration-free, so they are pinned.
    """
    legacy = {
        "profile_id": str(uuid4()),
        "resume_id": str(uuid4()),
        "score": 0.74,
        "matched_skills": ["A"],
        "missing_skills": ["B"],
    }

    score = ProfileMatchScore.model_validate(legacy)

    assert score.reasoning == ""
    assert score.hard_blocker is None
    assert score.insufficient_detail is False
