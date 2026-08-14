"""Normalized LLM failures.

Every failure inside this layer — transport, provider, or structured-output
validation — surfaces to callers as a single exception type carrying
`ErrorCode.LLM_PROVIDER_ERROR` (docs/architecture/shared-types.md#shared-error-codes,
docs/architecture/service-boundaries.md#llm-provider-layer). Domain
components therefore never need to know which provider is configured or
what its native exception hierarchy looks like.

`reason` is diagnostic detail *within* that single shared code, not a new
error code: it is what the retry policy keys off and what a log line or
`WorkflowError.message` can carry. It is deliberately not added to the
shared `ErrorCode` enum.
"""

from __future__ import annotations

from enum import Enum

from shared.errors.codes import ErrorCode

_RAW_TEXT_EXCERPT_LIMIT = 500


class LLMFailureReason(str, Enum):
    """Why an LLM call failed, one level below `LLM_PROVIDER_ERROR`."""

    TIMEOUT = "TIMEOUT"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"

    @property
    def retryable(self) -> bool:
        """Whether re-issuing the identical request could plausibly succeed.

        The two structured-output reasons are excluded: the same request
        would produce the same malformed shape, so they are handled by the
        repair loop (which re-prompts with the validation error) rather
        than by blind transport retries.
        """
        return self in _RETRYABLE_REASONS


_RETRYABLE_REASONS = frozenset(
    {
        LLMFailureReason.TIMEOUT,
        LLMFailureReason.CONNECTION_ERROR,
        LLMFailureReason.PROVIDER_ERROR,
    }
)


def excerpt(text: str, limit: int = _RAW_TEXT_EXCERPT_LIMIT) -> str:
    """Truncate provider text so a failure message stays log-sized."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [truncated, {len(text)} chars total]"


class LLMProviderError(Exception):
    """The only exception this layer raises to its callers."""

    error_code: ErrorCode = ErrorCode.LLM_PROVIDER_ERROR

    def __init__(
        self,
        reason: LLMFailureReason,
        message: str,
        *,
        provider: str,
        model: str,
        attempts: int = 1,
        raw_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.provider = provider
        self.model = model
        self.attempts = attempts
        self.raw_text = raw_text

    @property
    def retryable(self) -> bool:
        return self.reason.retryable

    def __str__(self) -> str:
        return (
            f"{self.error_code.value} ({self.reason.value}) from "
            f"provider={self.provider} model={self.model} "
            f"attempts={self.attempts}: {self.message}"
        )


__all__ = ["LLMFailureReason", "LLMProviderError", "excerpt"]
