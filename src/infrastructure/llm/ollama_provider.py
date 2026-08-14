"""Local/default `LLMProvider` implementation, backed by Ollama.

Ollama is the local-first default per about_project.md's technology table
("Ollama | Local LLM inference") and `config.py`'s Ollama-oriented defaults.
This module is the *only* place in the LLM Provider Layer that imports the
`ollama` SDK — no `ollama` type crosses the `LLMProvider`/`LLMClient`
boundary into a domain component
(docs/architecture/service-boundaries.md#llm-provider-layer). Every failure
mode the `ollama` client can raise (connection refused, timeout, HTTP/model
errors, unexpected response shape) is normalized into `LLMProviderError`
before it leaves `complete()`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import ollama

from infrastructure.llm.config import LLMConfig
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from infrastructure.llm.provider import LLMRequest, LLMResponse
from infrastructure.logging import format_context, get_logger

logger = get_logger(__name__)

# (host, timeout_seconds) -> an object exposing `.chat(**kwargs)` like
# `ollama.Client`. Overridable so tests never need a live Ollama server.
ClientFactory = Callable[[str, float], Any]


def _default_client_factory(host: str, timeout_seconds: float) -> ollama.Client:
    # Built fresh per call: `ollama.Client` fixes its timeout at construction
    # time, but `LLMRequest.timeout_seconds` is resolved per call (config
    # default, overridable per `LLMCallOptions`), so a single long-lived
    # client could not honor a caller's shorter/longer per-call timeout.
    return ollama.Client(host=host, timeout=timeout_seconds)


def _field(obj: Any, key: str) -> Any:
    """Read `key` off a real ollama response object or a dict-like test double."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


class OllamaProvider:
    """`LLMProvider` implementation that talks to a local Ollama server."""

    name = "ollama"

    def __init__(
        self,
        config: LLMConfig | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._config = config or LLMConfig.from_env()
        self._client_factory = client_factory or _default_client_factory
        logger.info(
            "LLM provider initialized | %s",
            format_context(provider=self.name, host=self._config.host, model=self._config.model),
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})

        client = self._client_factory(self._config.host, request.timeout_seconds)

        try:
            raw = client.chat(
                model=request.model,
                messages=messages,
                format=request.response_schema,
                options={"temperature": request.temperature},
            )
        except ConnectionError as exc:
            # ollama.Client wraps httpx.ConnectError (connection refused, DNS
            # failure, etc.) into the builtin ConnectionError.
            logger.error(
                "LLM request failed | %s",
                format_context(model=request.model, reason=LLMFailureReason.CONNECTION_ERROR.value, error=str(exc)),
            )
            raise self._error(LLMFailureReason.CONNECTION_ERROR, request, str(exc)) from exc
        except httpx.TimeoutException as exc:
            # Connect/read/write/pool timeouts propagate un-wrapped from the
            # underlying httpx client.
            logger.error(
                "LLM request failed | %s",
                format_context(model=request.model, reason=LLMFailureReason.TIMEOUT.value, error=str(exc)),
            )
            raise self._error(LLMFailureReason.TIMEOUT, request, str(exc)) from exc
        except ollama.ResponseError as exc:
            # Non-2xx HTTP response, e.g. model not found (404).
            status = getattr(exc, "status_code", "unknown")
            logger.error(
                "LLM request failed | %s",
                format_context(model=request.model, reason=LLMFailureReason.PROVIDER_ERROR.value, status_code=status),
            )
            raise self._error(
                LLMFailureReason.PROVIDER_ERROR,
                request,
                f"ollama response error (status_code={status}): {exc}",
            ) from exc
        except ollama.RequestError as exc:
            # Malformed request rejected client-side before hitting the wire.
            logger.error(
                "LLM request failed | %s",
                format_context(model=request.model, reason=LLMFailureReason.PROVIDER_ERROR.value, error=str(exc)),
            )
            raise self._error(LLMFailureReason.PROVIDER_ERROR, request, str(exc)) from exc
        except Exception as exc:  # safety net; never leak a vendor exception
            logger.exception(
                "LLM request failed with an unexpected error | %s",
                format_context(model=request.model),
            )
            raise self._error(
                LLMFailureReason.PROVIDER_ERROR, request, f"unexpected ollama error: {exc}"
            ) from exc

        message = _field(raw, "message")
        content = _field(message, "content")
        if not content:
            raise self._error(
                LLMFailureReason.INVALID_RESPONSE,
                request,
                "ollama response contained no message content",
            )

        return LLMResponse(
            text=content,
            model=_field(raw, "model") or request.model,
            prompt_tokens=_field(raw, "prompt_eval_count"),
            completion_tokens=_field(raw, "eval_count"),
        )

    def _error(self, reason: LLMFailureReason, request: LLMRequest, message: str) -> LLMProviderError:
        return LLMProviderError(reason, message, provider=self.name, model=request.model)


__all__ = ["ClientFactory", "OllamaProvider"]
