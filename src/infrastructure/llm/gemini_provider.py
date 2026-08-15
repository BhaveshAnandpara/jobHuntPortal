"""Cloud `LLMProvider` implementation, backed by the Gemini API.

Local Ollama inference (`ollama_provider.py`) proved impractical on this
deployment's hardware: no GPU, and CPU-bound grammar-constrained decoding
stalling past a 600s timeout even on a 7B model. This provider is a
pragmatic, deployment-scoped override of about_project.md's local-first
default (selected via `LLMConfig.provider`) — the documented architecture
still names Ollama; this does not change it.

This module is the only place in the LLM Provider Layer that imports the
`google-genai` SDK — no vendor type crosses the `LLMProvider`/`LLMClient`
boundary into a domain component
(docs/architecture/service-boundaries.md#llm-provider-layer). Every failure
mode the SDK can raise (connection error, timeout, API error, unexpected
response shape) is normalized into `LLMProviderError` before it leaves
`complete()`.
"""

from __future__ import annotations

import httpx
from google import genai
from google.genai import errors, types

from infrastructure.llm.config import LLMConfig
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from infrastructure.llm.provider import LLMRequest, LLMResponse
from infrastructure.logging import format_context, get_logger

logger = get_logger(__name__)


class GeminiProvider:
    """`LLMProvider` implementation that talks to the Gemini API."""

    name = "gemini"

    def __init__(self, config: LLMConfig | None = None) -> None:
        self._config = config or LLMConfig.from_env()
        if not self._config.api_key:
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        self._client = genai.Client(api_key=self._config.api_key)
        logger.info(
            "LLM provider initialized | %s",
            format_context(provider=self.name, model=self._config.model),
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        config = types.GenerateContentConfig(
            system_instruction=request.system,
            temperature=request.temperature,
            response_mime_type="application/json" if request.response_schema else None,
            response_schema=request.response_schema,
            # Gemini's `HttpOptions.timeout` is milliseconds; `LLMRequest`
            # carries seconds, resolved per call same as `OllamaProvider`.
            http_options=types.HttpOptions(timeout=int(request.timeout_seconds * 1000)),
        )

        try:
            response = self._client.models.generate_content(
                model=request.model,
                contents=request.prompt,
                config=config,
            )
        except httpx.TimeoutException as exc:
            logger.error(
                "LLM request failed | %s",
                format_context(model=request.model, reason=LLMFailureReason.TIMEOUT.value, error=str(exc)),
            )
            raise self._error(LLMFailureReason.TIMEOUT, request, str(exc)) from exc
        except httpx.ConnectError as exc:
            logger.error(
                "LLM request failed | %s",
                format_context(model=request.model, reason=LLMFailureReason.CONNECTION_ERROR.value, error=str(exc)),
            )
            raise self._error(LLMFailureReason.CONNECTION_ERROR, request, str(exc)) from exc
        except errors.APIError as exc:
            # Non-2xx response from the Gemini API, e.g. bad API key, quota
            # exceeded, invalid model name.
            logger.error(
                "LLM request failed | %s",
                format_context(model=request.model, reason=LLMFailureReason.PROVIDER_ERROR.value, status_code=exc.code),
            )
            raise self._error(
                LLMFailureReason.PROVIDER_ERROR,
                request,
                f"gemini API error (code={exc.code}): {exc.message}",
            ) from exc
        except Exception as exc:  # safety net; never leak a vendor exception
            logger.exception(
                "LLM request failed with an unexpected error | %s",
                format_context(model=request.model),
            )
            raise self._error(
                LLMFailureReason.PROVIDER_ERROR, request, f"unexpected gemini error: {exc}"
            ) from exc

        text = response.text
        if not text:
            raise self._error(
                LLMFailureReason.INVALID_RESPONSE,
                request,
                "gemini response contained no text",
            )

        usage = response.usage_metadata
        return LLMResponse(
            text=text,
            model=request.model,
            prompt_tokens=usage.prompt_token_count if usage else None,
            completion_tokens=usage.candidates_token_count if usage else None,
        )

    def _error(self, reason: LLMFailureReason, request: LLMRequest, message: str) -> LLMProviderError:
        return LLMProviderError(reason, message, provider=self.name, model=request.model)


__all__ = ["GeminiProvider"]
