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


class LlmNotConfiguredError(Exception):
    """The user has no usable LLM provider configuration on file.

    Raised by `infrastructure.llm.user_client.get_user_llm_client` when the
    `user_llm_config` row is missing, has never been validated against the
    live provider (`last_validated_at is None`), or stores no key. It means
    "this user must go configure a key" — a *user action*, not a provider
    failure — which is why it deliberately does **not** subclass
    `LLMProviderError`: every existing `except LLMProviderError` handler in
    the workflows treats its exception as a transient model failure worth a
    retry or a degraded fallback, and silently swallowing a
    not-configured-at-all condition into that path would hide the one thing
    the user actually needs to be told.

    `error_code` is `LLM_PROVIDER_ERROR` only as an interim mapping: the
    dedicated `ErrorCode.LLM_NOT_CONFIGURED` and its 4xx mapping belong to
    the gating task (docs/llm-provider-config/TECH.md Task 7), which owns
    `shared/errors/codes.py`.
    """

    error_code: ErrorCode = ErrorCode.LLM_PROVIDER_ERROR

    def __init__(self, user_id: object, message: str | None = None) -> None:
        self.user_id = user_id
        self.message = message or (
            f"user {user_id} has no validated LLM provider configuration; "
            "add an AI provider key in Settings to use this feature"
        )
        super().__init__(self.message)


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


__all__ = [
    "LLMFailureReason",
    "LLMProviderError",
    "LlmNotConfiguredError",
    "excerpt",
]
