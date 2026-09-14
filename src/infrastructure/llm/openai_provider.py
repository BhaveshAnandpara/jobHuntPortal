"""Cloud `LLMProvider` implementation, backed by the OpenAI API.

One of the providers a user can select for their own account (see
docs/llm-provider-config/TECH.md) alongside Groq (`groq_provider.py`),
Gemini (`gemini_provider.py`), Claude (`anthropic_provider.py`) and the
local Ollama fallback (`ollama_provider.py`).

This module is the only place in the LLM Provider Layer that imports the
`openai` SDK — no vendor type crosses the `LLMProvider`/`LLMClient`
boundary into a domain component
(docs/architecture/service-boundaries.md#llm-provider-layer). Every failure
mode the SDK can raise (connection error, timeout, rate limit, other
API/server errors) is normalized into `LLMProviderError` before it leaves
`complete()`.
"""

from __future__ import annotations

import re
from typing import Any

import openai

from infrastructure.llm.config import DEFAULT_MODEL, LLMConfig
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from infrastructure.llm.provider import LLMRequest, LLMResponse
from infrastructure.logging import format_context, get_logger

logger = get_logger(__name__)

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
"""Model used when no OpenAI-specific override is configured. Chosen as the
cheapest current chat model that still supports `response_format`'s
`json_schema` mode, which `LLMClient`'s structured-output path relies on."""

_INVALID_SCHEMA_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def _schema_name(response_schema: dict[str, Any]) -> str:
    """Derive OpenAI's required `json_schema.name` (`^[a-zA-Z0-9_-]{1,64}$`)
    from the schema's Pydantic-generated `title` (e.g.
    `"ExtractedResumeProfile"`), which already matches that pattern for
    every schema in this codebase — the sanitize/truncate/fallback below is
    only a defensive backstop, not expected to trigger.

    Deliberately duplicated from `groq_provider.py` rather than shared:
    each provider module owns its own vendor-shaped details, and importing
    one provider from another would drag that provider's SDK in with it.
    """
    name = _INVALID_SCHEMA_NAME_CHARS.sub("_", response_schema.get("title") or "response")
    return name[:64] or "response"


def _resolve_model(model: str | None) -> str:
    """Fall back to `DEFAULT_OPENAI_MODEL` when no OpenAI model was chosen.

    `LLMConfig.model`'s layer-wide default is Ollama's `llama3` placeholder,
    which means nothing to the OpenAI API — treat it as "unset" rather than
    sending it and getting a 404 model-not-found back.
    """
    if not model or model == DEFAULT_MODEL:
        return DEFAULT_OPENAI_MODEL
    return model


class OpenAIProvider:
    """`LLMProvider` implementation that talks to the OpenAI API."""

    name = "openai"

    def __init__(
        self, config: LLMConfig | None = None, client: openai.OpenAI | None = None
    ) -> None:
        self._config = config or LLMConfig.from_env()
        self._model = _resolve_model(self._config.model)
        if client is None:
            if not self._config.api_key:
                raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
            client = openai.OpenAI(api_key=self._config.api_key)
        self._client = client
        logger.info(
            "LLM provider initialized | %s",
            format_context(provider=self.name, model=self._model),
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        model = _resolve_model(request.model)

        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": request.temperature,
            "timeout": request.timeout_seconds,
        }
        if request.response_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": _schema_name(request.response_schema),
                    "schema": request.response_schema,
                },
            }

        try:
            response = self._client.chat.completions.create(**kwargs)
        except openai.APITimeoutError as exc:
            logger.error(
                "LLM request failed | %s",
                format_context(model=model, reason=LLMFailureReason.TIMEOUT.value, error=str(exc)),
            )
            raise self._error(LLMFailureReason.TIMEOUT, model, str(exc)) from exc
        except openai.APIConnectionError as exc:
            # Subclass check order matters: `APITimeoutError` is itself a
            # subclass of `APIConnectionError`, so it must be caught first.
            logger.error(
                "LLM request failed | %s",
                format_context(model=model, reason=LLMFailureReason.CONNECTION_ERROR.value, error=str(exc)),
            )
            raise self._error(LLMFailureReason.CONNECTION_ERROR, model, str(exc)) from exc
        except openai.RateLimitError as exc:
            # Distinguished in the log line (and via its own except branch)
            # even though it maps to the same `PROVIDER_ERROR` reason as any
            # other API-level failure below — `LLMFailureReason` has no
            # dedicated rate-limit value, and adding one is a
            # provider-layer-wide taxonomy change out of scope here.
            # `PROVIDER_ERROR` is retryable, so the existing
            # transport-retry/backoff in `_call_with_retry` already applies,
            # which is the correct behavior for a 429. Same treatment as
            # `GroqProvider`.
            logger.error(
                "LLM request failed | %s",
                format_context(
                    model=model, reason=LLMFailureReason.PROVIDER_ERROR.value, status_code=429, rate_limited=True
                ),
            )
            raise self._error(LLMFailureReason.PROVIDER_ERROR, model, f"rate limited: {exc}") from exc
        except openai.APIStatusError as exc:
            # Every other non-2xx response: auth failure, bad request, model
            # not found, server error, etc. Same
            # retryable-by-architecture-precedent note as `GroqProvider` — a
            # genuinely non-retryable client error (e.g. bad API key) still
            # gets retried by `_call_with_retry` under this reason,
            # consistent with how every other provider in this layer already
            # behaves rather than an OpenAI-specific carve-out.
            logger.error(
                "LLM request failed | %s",
                format_context(
                    model=model, reason=LLMFailureReason.PROVIDER_ERROR.value, status_code=exc.status_code
                ),
            )
            raise self._error(
                LLMFailureReason.PROVIDER_ERROR,
                model,
                f"openai API error (status_code={exc.status_code}): {exc}",
            ) from exc
        except Exception as exc:  # safety net; never leak a vendor exception
            logger.exception(
                "LLM request failed with an unexpected error | %s",
                format_context(model=model),
            )
            raise self._error(
                LLMFailureReason.PROVIDER_ERROR, model, f"unexpected openai error: {exc}"
            ) from exc

        choice = response.choices[0] if response.choices else None
        text = choice.message.content if choice and choice.message else None
        if not text:
            raise self._error(
                LLMFailureReason.INVALID_RESPONSE,
                model,
                "openai response contained no text",
            )

        usage = response.usage
        return LLMResponse(
            text=text,
            model=response.model or model,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )

    def _error(self, reason: LLMFailureReason, model: str, message: str) -> LLMProviderError:
        return LLMProviderError(reason, message, provider=self.name, model=model)


__all__ = ["DEFAULT_OPENAI_MODEL", "OpenAIProvider"]
