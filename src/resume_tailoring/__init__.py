"""Resume Tailoring Service — on-demand, job-specific resume generation.

See docs/tailored-resume/PRODUCT.md and docs/tailored-resume/TECH.md. This
package owns the tailoring prompt, the reuse of Job Matching Service's
scoring mechanism for an apples-to-apples comparison, and the 5-point gate
that decides whether a tailored resume is worth producing at all.

A component rather than a node in the job-matching graph, and a synchronous
on-demand call rather than a Kafka consumer — both decisions and their
rationale are recorded in TECH.md's Architecture Overview.
"""

from resume_tailoring.errors import TailoringError
from resume_tailoring.service import (
    MINIMUM_SCORE_IMPROVEMENT,
    ResumeTailoringService,
    TailoringResult,
    tailor_resume,
)

__all__ = [
    "MINIMUM_SCORE_IMPROVEMENT",
    "ResumeTailoringService",
    "TailoringError",
    "TailoringResult",
    "tailor_resume",
]
