"""The provider abstraction that decouples callers from a specific vendor.

`LLMProvider` is the *only* seam a new vendor implements. It is deliberately
narrow: one text-in/text-out call. Retries, structured-output parsing, and
repair prompting all live one level up in `LLMClient`, so adding a provider
never means reimplementing them.

Nothing here is provider-specific: `LLMRequest`/`LLMResponse` are plain
Pydantic models, so no vendor SDK type reaches a domain component
(docs/architecture/service-boundaries.md#llm-provider-layer).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class LLMRequest(BaseModel):
    """One fully-resolved inference call. All knobs are already merged."""

    prompt: str
    system: str | None = None
    model: str
    temperature: float = Field(ge=0.0, le=2.0)
    timeout_seconds: float = Field(gt=0.0)
    response_schema: dict[str, Any] | None = None
    """JSON Schema the provider should constrain generation to, when it
    supports native structured output. Providers that don't may ignore it —
    `LLMClient` also states the schema in the prompt and validates the
    result either way."""


class LLMResponse(BaseModel):
    """Raw provider output, normalized. Never a vendor response object."""

    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """Implementations must raise `LLMProviderError` for every failure mode;
    `LLMClient` wraps anything else as a safety net, but a provider that
    relies on that is not normalizing its own vendor exceptions."""

    name: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...


__all__ = ["LLMProvider", "LLMRequest", "LLMResponse"]
