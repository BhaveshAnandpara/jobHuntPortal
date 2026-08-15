"""Cloud `LLMProvider` implementation, backed by the Groq API.

Groq is the primary provider for this deployment: fast hosted inference,
native JSON-schema structured output, and (per `about_project.md`'s
free/local-first strategy) a free tier — see `gemini_provider.py` for the
provider used as an alternative, and `ollama_provider.py` for the local
alternative when no cloud provider is configured.

This module is the only place in the LLM Provider Layer that imports the
`groq` SDK — no vendor type crosses the `LLMProvider`/`LLMClient` boundary
into a domain component
(docs/architecture/service-boundaries.md#llm-provider-layer). Every failure
mode the SDK can raise (connection error, timeout, rate limit, other
API/server errors) is normalized into `LLMProviderError` before it leaves
`complete()`.
"""

from __future__ import annotations

import re
from typing import Any

import groq

from infrastructure.llm.config import LLMConfig
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from infrastructure.llm.provider import LLMRequest, LLMResponse
from infrastructure.logging import format_context, get_logger

logger = get_logger(__name__)

_INVALID_SCHEMA_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def _schema_name(response_schema: dict[str, Any]) -> str:
    """Derive Groq's required `json_schema.name` (`^[a-zA-Z0-9_-]{1,64}$`)
    from the schema's Pydantic-generated `title` (e.g.
    `"ExtractedResumeProfile"`), which already matches that pattern for
    every schema in this codebase — the sanitize/truncate/fallback below is
    only a defensive backstop, not expected to trigger."""
    name = _INVALID_SCHEMA_NAME_CHARS.sub("_", response_schema.get("title") or "response")
    return name[:64] or "response"


class GroqProvider:
    """`LLMProvider` implementation that talks to the Groq API."""

    name = "groq"

    def __init__(self, config: LLMConfig | None = None, client: groq.Groq | None = None) -> None:
        self._config = config or LLMConfig.from_env()
        if client is None:
            if not self._config.api_key:
                raise ValueError("GROQ_API_KEY is required when LLM_PROVIDER=groq")
            client = groq.Groq(api_key=self._config.api_key)
        self._client = client
        logger.info(
            "LLM provider initialized | %s",
            format_context(provider=self.name, model=self._config.model),
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})

        kwargs: dict[str, Any] = {
            "model": request.model,
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
        except groq.APITimeoutError as exc:
            logger.error(
                "LLM request failed | %s",
                format_context(model=request.model, reason=LLMFailureReason.TIMEOUT.value, error=str(exc)),
            )
            raise self._error(LLMFailureReason.TIMEOUT, request, str(exc)) from exc
        except groq.APIConnectionError as exc:
            # Subclass check order matters: `APITimeoutError` is itself a
            # subclass of `APIConnectionError`, so it must be caught first.
            logger.error(
                "LLM request failed | %s",
                format_context(model=request.model, reason=LLMFailureReason.CONNECTION_ERROR.value, error=str(exc)),
            )
            raise self._error(LLMFailureReason.CONNECTION_ERROR, request, str(exc)) from exc
        except groq.RateLimitError as exc:
            # Distinguished from other API errors in the log line (and via
            # its own except branch) even though it maps to the same
            # `PROVIDER_ERROR` reason as any other API-level failure below —
            # `LLMFailureReason` has no dedicated rate-limit value, and
            # adding one is a provider-layer-wide taxonomy change out of
            # scope here. `PROVIDER_ERROR` is retryable, so the existing
            # transport-retry/backoff in `_call_with_retry` already applies,
            # which is the correct behavior for a 429.
            logger.error(
                "LLM request failed | %s",
                format_context(
                    model=request.model, reason=LLMFailureReason.PROVIDER_ERROR.value, status_code=429, rate_limited=True
                ),
            )
            raise self._error(LLMFailureReason.PROVIDER_ERROR, request, f"rate limited: {exc}") from exc
        except groq.APIStatusError as exc:
            # Every other non-2xx response: auth failure, bad request,
            # model not found, server error, etc. Same
            # retryable-by-architecture-precedent note as `OllamaProvider`'s
            # 404 "model not found" case — a genuinely non-retryable client
            # error (e.g. bad API key) still gets retried by
            # `_call_with_retry` under this reason, consistent with how
            # every other provider in this layer already behaves rather
            # than a Groq-specific carve-out.
            logger.error(
                "LLM request failed | %s",
                format_context(
                    model=request.model, reason=LLMFailureReason.PROVIDER_ERROR.value, status_code=exc.status_code
                ),
            )
            raise self._error(
                LLMFailureReason.PROVIDER_ERROR,
                request,
                f"groq API error (status_code={exc.status_code}): {exc}",
            ) from exc
        except Exception as exc:  # safety net; never leak a vendor exception
            logger.exception(
                "LLM request failed with an unexpected error | %s",
                format_context(model=request.model),
            )
            raise self._error(
                LLMFailureReason.PROVIDER_ERROR, request, f"unexpected groq error: {exc}"
            ) from exc

        choice = response.choices[0] if response.choices else None
        text = choice.message.content if choice and choice.message else None
        if not text:
            raise self._error(
                LLMFailureReason.INVALID_RESPONSE,
                request,
                "groq response contained no text",
            )

        usage = response.usage
        return LLMResponse(
            text=text,
            model=response.model or request.model,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )

    def _error(self, reason: LLMFailureReason, request: LLMRequest, message: str) -> LLMProviderError:
        return LLMProviderError(reason, message, provider=self.name, model=request.model)


__all__ = ["GroqProvider"]
