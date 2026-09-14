"""Cloud `LLMProvider` implementation, backed by the Anthropic (Claude) API.

Claude is one of the user-selectable providers per
docs/llm-provider-config/TECH.md — alongside Groq (`groq_provider.py`),
Gemini (`gemini_provider.py`), and the local Ollama alternative
(`ollama_provider.py`).

This module is the only place in the LLM Provider Layer that imports the
`anthropic` SDK — no vendor type crosses the `LLMProvider`/`LLMClient`
boundary into a domain component
(docs/architecture/service-boundaries.md#llm-provider-layer). Every failure
mode the SDK can raise (connection error, timeout, rate limit, other
API/server errors) is normalized into `LLMProviderError` before it leaves
`complete()`.

Three deliberate differences from the Groq/Gemini providers, all forced by
Anthropic's own API shape rather than by preference:

1. `max_tokens` is **required** by the Messages API. `LLMRequest` carries no
   such field (it is a shared contract this task must not widen), so
   `DEFAULT_MAX_TOKENS` below supplies it.
2. `temperature` is **not sent**. The `anthropic` 1.x Messages API surface no
   longer exposes a `temperature` parameter at all — current Claude models
   reject sampling parameters — so `LLMRequest.temperature` cannot be
   forwarded. Every consumer in this codebase runs at `temperature=0.0` for
   reproducibility; Claude's own default is already low-variance, and the
   structured-output validation/repair loop in `LLMClient` is what actually
   guards response shape here.
3. `LLMRequest.response_schema` is **ignored**, which the `LLMProvider`
   protocol explicitly allows ("Providers that don't may ignore it").
   Anthropic's native structured output requires a restricted JSON Schema
   subset (`additionalProperties: false`, every property required, no
   `$ref`/`$defs`), which Pydantic's `model_json_schema()` output in this
   codebase does not satisfy — sending it would 400. `LLMClient` states the
   schema in the prompt and validates the result either way, which is the
   path every Claude call here relies on.
"""

from __future__ import annotations

from typing import Any

import anthropic

from infrastructure.llm.config import DEFAULT_MODEL, LLMConfig
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from infrastructure.llm.provider import LLMRequest, LLMResponse
from infrastructure.logging import format_context, get_logger

logger = get_logger(__name__)

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
"""Model used when no Claude model was configured for the caller. Exposed
so the per-user client factory can fall back to the same id rather than
duplicating the string."""

DEFAULT_MAX_TOKENS = 8192
"""Output ceiling for a single call. Required by the Messages API and not
expressible on the shared `LLMRequest`. Sized for the structured-extraction
payloads this layer actually produces (resume profiles, job fields, scoring
and outreach objects) with headroom, while staying small enough that a
non-streaming request completes inside `LLMRequest.timeout_seconds`."""


def _resolve_model(model: str) -> str:
    """Return the Claude model id to call.

    `LLMConfig.model` defaults to the layer-wide generic `DEFAULT_MODEL`
    (an Ollama model name) when nothing provider-specific was resolved, and
    that value is meaningless to Anthropic. Treating it — and an empty
    string — as "unset" is what makes `DEFAULT_ANTHROPIC_MODEL` the default
    when no override is given, without this provider reaching into the
    shared config module to register its own default.
    """
    if not model or model == DEFAULT_MODEL:
        return DEFAULT_ANTHROPIC_MODEL
    return model


class AnthropicProvider:
    """`LLMProvider` implementation that talks to the Anthropic API."""

    name = "claude"

    def __init__(
        self,
        config: LLMConfig | None = None,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self._config = config or LLMConfig.from_env()
        self._model = _resolve_model(self._config.model)
        if client is None:
            if not self._config.api_key:
                raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=claude")
            client = anthropic.Anthropic(api_key=self._config.api_key)
        self._client = client
        logger.info(
            "LLM provider initialized | %s",
            format_context(provider=self.name, model=self._model),
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        model = _resolve_model(request.model)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "messages": [{"role": "user", "content": request.prompt}],
            "timeout": request.timeout_seconds,
        }
        if request.system:
            # Anthropic carries the system prompt as its own top-level
            # parameter, not as a `system`-role message like Groq does.
            kwargs["system"] = request.system

        try:
            response = self._client.messages.create(**kwargs)
        except anthropic.APITimeoutError as exc:
            logger.error(
                "LLM request failed | %s",
                format_context(model=model, reason=LLMFailureReason.TIMEOUT.value, error=str(exc)),
            )
            raise self._error(LLMFailureReason.TIMEOUT, model, str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            # Subclass check order matters: `APITimeoutError` is itself a
            # subclass of `APIConnectionError`, so it must be caught first.
            logger.error(
                "LLM request failed | %s",
                format_context(model=model, reason=LLMFailureReason.CONNECTION_ERROR.value, error=str(exc)),
            )
            raise self._error(LLMFailureReason.CONNECTION_ERROR, model, str(exc)) from exc
        except anthropic.RateLimitError as exc:
            # Same treatment as `GroqProvider`'s 429 branch: distinguished in
            # the log line, but mapped to `PROVIDER_ERROR` because
            # `LLMFailureReason` has no dedicated rate-limit value. That
            # reason is retryable, so `_call_with_retry`'s existing backoff
            # already does the right thing for a 429.
            logger.error(
                "LLM request failed | %s",
                format_context(
                    model=model, reason=LLMFailureReason.PROVIDER_ERROR.value, status_code=429, rate_limited=True
                ),
            )
            raise self._error(LLMFailureReason.PROVIDER_ERROR, model, f"rate limited: {exc}") from exc
        except anthropic.APIStatusError as exc:
            # Every other non-2xx response: auth failure, bad request,
            # model not found, server error, etc. Retried under
            # `PROVIDER_ERROR` even when genuinely non-retryable (e.g. a bad
            # API key), consistent with every other provider in this layer
            # rather than an Anthropic-specific carve-out.
            logger.error(
                "LLM request failed | %s",
                format_context(
                    model=model, reason=LLMFailureReason.PROVIDER_ERROR.value, status_code=exc.status_code
                ),
            )
            raise self._error(
                LLMFailureReason.PROVIDER_ERROR,
                model,
                f"anthropic API error (status_code={exc.status_code}): {exc}",
            ) from exc
        except Exception as exc:  # safety net; never leak a vendor exception
            logger.exception(
                "LLM request failed with an unexpected error | %s",
                format_context(model=model),
            )
            raise self._error(
                LLMFailureReason.PROVIDER_ERROR, model, f"unexpected anthropic error: {exc}"
            ) from exc

        text = self._extract_text(response)
        if not text:
            # A safety refusal is a 200 with `stop_reason="refusal"` and no
            # usable text, so it lands here rather than in an except branch —
            # calling it out keeps the failure message diagnosable instead of
            # a bare "no text".
            stop_reason = getattr(response, "stop_reason", None)
            detail = (
                "anthropic refused the request (stop_reason=refusal)"
                if stop_reason == "refusal"
                else "anthropic response contained no text"
            )
            raise self._error(LLMFailureReason.INVALID_RESPONSE, model, detail)

        usage = getattr(response, "usage", None)
        return LLMResponse(
            text=text,
            model=getattr(response, "model", None) or model,
            prompt_tokens=usage.input_tokens if usage else None,
            completion_tokens=usage.output_tokens if usage else None,
        )

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Join every `text` block of the response.

        Anthropic returns `content` as a *list of blocks*, not a single
        string: a plain answer is one `TextBlock`, but a response can also
        carry `thinking`/`tool_use` blocks that have no `.text`. Filtering on
        block type is what keeps this from breaking if a caller ever enables
        those.
        """
        blocks = getattr(response, "content", None) or []
        return "".join(
            block.text for block in blocks if getattr(block, "type", None) == "text" and getattr(block, "text", None)
        )

    def _error(self, reason: LLMFailureReason, model: str, message: str) -> LLMProviderError:
        return LLMProviderError(reason, message, provider=self.name, model=model)


__all__ = ["DEFAULT_ANTHROPIC_MODEL", "DEFAULT_MAX_TOKENS", "AnthropicProvider"]
