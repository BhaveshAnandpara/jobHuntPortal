"""Local error type for Resume Tailoring Service.

Same layering as `profiles.service.ProfileError` and `users.service.UserError`:
one typed exception carrying a shared `ErrorCode`, which the API layer
(docs/tailored-resume/TECH.md Task 6) maps to an HTTP status. This component
is on-demand and synchronous — it has no Kafka consumer — so unlike
`matching.errors.MatchingError` there is no retry/DLQ classification
contract to satisfy here; the code exists so a caller can distinguish
"this job isn't yours / doesn't exist" (`NOT_FOUND`) from "the LLM failed"
(`LLM_PROVIDER_ERROR`) without string-matching a message.
"""

from __future__ import annotations

from shared.errors.codes import ErrorCode


class TailoringError(Exception):
    """A Resume Tailoring Service failure carrying a shared `ErrorCode`.

    `NOT_FOUND` deliberately covers *both* "no `JobMatch` exists for this
    job" and "a `JobMatch` exists but belongs to a different user". A
    caller must not be able to tell those two apart: a distinguishable
    "forbidden" response would confirm the existence of another user's
    matched job, which is exactly the leak the ownership check exists to
    prevent.
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


__all__ = ["TailoringError"]
