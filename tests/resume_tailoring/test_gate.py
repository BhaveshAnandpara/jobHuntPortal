"""The 5-point gate's arithmetic, and the identity of the scorer behind it.

Separate from `test_service.py` only because these are synchronous — they
test two pure properties that need no service, no fakes, and no event loop.
`test_service.py` covers the gate's behaviour end-to-end through
`tailor_resume`.
"""

from __future__ import annotations

import inspect

import pytest

from resume_tailoring.service import (
    MINIMUM_SCORE_IMPROVEMENT,
    ResumeTailoringService,
    clears_gate,
)


def test_the_gate_is_five_points() -> None:
    """docs/tailored-resume/PRODUCT.md: "at least 5 percentage points
    higher". Scores are 0.0-1.0, so five points is 0.05.
    """
    assert MINIMUM_SCORE_IMPROVEMENT == 0.05


@pytest.mark.parametrize(
    ("base", "tailored", "expected"),
    [
        # Exactly five points. Several of these pairs subtract to just
        # *under* 0.05 in binary floating point (0.75 - 0.70 is
        # 0.04999999999999993), so a naive `>= 0.05` would wrongly reject a
        # genuine, user-visible five-point improvement.
        (0.70, 0.75, True),
        (0.35, 0.40, True),
        (0.62, 0.67, True),
        (0.0, 0.05, True),
        (0.95, 1.0, True),
        # Comfortably over.
        (0.40, 0.90, True),
        # Genuinely under the bar — including a hair under, which must not
        # be swept in by the float tolerance.
        (0.70, 0.7499, False),
        (0.62, 0.6699, False),
        (0.0, 0.0499, False),
        (0.5, 0.5, False),
        # A tailored version that scores worse is not an improvement.
        (0.8, 0.4, False),
    ],
)
def test_gate_arithmetic(base: float, tailored: float, expected: bool) -> None:
    assert clears_gate(base, tailored) is expected


def test_production_default_is_the_unmodified_job_matching_scorer() -> None:
    """The 5-point promise is only meaningful if the tailored content is
    measured with the same yardstick as the original, so the default scorer
    must be `score_profile_with_llm` *itself* — not a copy, not a wrapper.
    """
    from workflows.langgraph.job_matching import scoring

    signature = inspect.signature(ResumeTailoringService.__init__)
    assert signature.parameters["score"].default is scoring.score_profile_with_llm
