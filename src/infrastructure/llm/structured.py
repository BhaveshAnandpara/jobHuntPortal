"""Structured-output adapter: model text -> a validated Pydantic instance.

Callers hand this layer a Pydantic model class and get an instance of it
back. Two failure modes are distinguished, because they need different
repair prompts: the response wasn't JSON at all (`INVALID_RESPONSE`), or it
was JSON that doesn't fit the schema (`SCHEMA_VALIDATION_FAILED`).

`LLMClient` is the single public entry point domain components use
(docs/architecture/service-boundaries.md#llm-provider-layer): it resolves
`LLMConfig`/`LLMCallOptions`, drives the injected `LLMProvider` through
`LLMConfig.max_attempts` transport-level retries for retryable failures, and
drives up to `LLMConfig.repair_attempts` re-prompts for structured-output
failures (a different retry budget on purpose — see
`LLMFailureReason.retryable`). A caller that doesn't inject a provider gets
the local-first default (`OllamaProvider` built from `LLMConfig.from_env()`).
"""

from __future__ import annotations

import json
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from infrastructure.llm.config import LLMCallOptions, LLMConfig
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError, excerpt
from infrastructure.llm.provider import LLMProvider, LLMRequest

T = TypeVar("T", bound=BaseModel)


def schema_instructions(schema: type[BaseModel]) -> str:
    """Prompt text stating the required output shape.

    Sent in addition to any native structured-output support the provider
    has, so a provider that ignores `LLMRequest.response_schema` still has a
    fair chance of producing a parseable object.
    """
    rendered = json.dumps(schema.model_json_schema(), indent=2, sort_keys=True)
    return (
        "Respond with a single JSON object and nothing else. Do not wrap it "
        "in markdown fences and do not add commentary before or after it. "
        f"The object must conform to this JSON Schema:\n{rendered}"
    )


def extract_json_object(text: str) -> str:
    """Pull the outermost JSON object out of a response.

    Small models routinely wrap output in prose or markdown fences even when
    told not to; slicing between the first `{` and last `}` recovers those
    without a full parser.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("response contains no JSON object")
    return text[start : end + 1]


def parse_structured(
    text: str,
    schema: type[T],
    *,
    provider: str,
    model: str,
    attempts: int = 1,
) -> T:
    try:
        candidate = extract_json_object(text)
        parsed = json.loads(candidate)
    except (ValueError, json.JSONDecodeError) as exc:
        raise LLMProviderError(
            LLMFailureReason.INVALID_RESPONSE,
            f"response was not parseable JSON: {exc}",
            provider=provider,
            model=model,
            attempts=attempts,
            raw_text=text,
        ) from exc

    try:
        return schema.model_validate(parsed)
    except ValidationError as exc:
        raise LLMProviderError(
            LLMFailureReason.SCHEMA_VALIDATION_FAILED,
            f"response did not match {schema.__name__}: {exc}",
            provider=provider,
            model=model,
            attempts=attempts,
            raw_text=text,
        ) from exc


def repair_prompt(original_prompt: str, raw_response: str, problem: str) -> str:
    """Re-ask, showing the model exactly what was wrong with its last answer."""
    return (
        f"{original_prompt}\n\n"
        "Your previous response could not be used.\n\n"
        f"Previous response:\n{excerpt(raw_response)}\n\n"
        f"Problem: {problem}\n\n"
        "Return only the corrected JSON object."
    )


def _default_provider(config: LLMConfig) -> LLMProvider:
    # Imported lazily to avoid a module-load-time dependency on the `ollama`
    # SDK for callers that always inject their own provider (e.g. tests).
    from infrastructure.llm.ollama_provider import OllamaProvider

    return OllamaProvider(config=config)


class LLMClient:
    """Executes a prompt against an `LLMProvider` and returns a validated
    instance of the caller's requested Pydantic schema.

    This is the only object domain components construct/inject to reach the
    LLM Provider Layer. Callers never see a vendor type, a raw provider
    response, or an un-normalized exception.
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
        config: LLMConfig | None = None,
    ) -> None:
        self._config = config or LLMConfig.from_env()
        self._provider = provider or _default_provider(self._config)

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    def complete_structured(
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
        options: LLMCallOptions | None = None,
    ) -> T:
        """Run `prompt`, returning a validated `schema` instance.

        Re-prompts (repair) on structured-output failures up to
        `LLMConfig.repair_attempts` times, showing the model its previous
        malformed answer and why it was rejected. Transport-level failures
        (timeout, connection error, provider error) are retried separately,
        inside each individual call, per `LLMConfig.max_attempts` — they are
        not repair rounds and do not consume the repair budget.
        """
        cfg = self._config.merged(options)
        response_schema = schema.model_json_schema()
        base_prompt = f"{prompt}\n\n{schema_instructions(schema)}"
        current_prompt = base_prompt
        last_error: LLMProviderError | None = None

        for repair_round in range(cfg.repair_attempts + 1):
            text = self._call_with_retry(current_prompt, system, cfg, response_schema)
            try:
                return parse_structured(
                    text,
                    schema,
                    provider=self._provider.name,
                    model=cfg.model,
                    attempts=repair_round + 1,
                )
            except LLMProviderError as exc:
                last_error = exc
                if repair_round >= cfg.repair_attempts:
                    raise
                current_prompt = repair_prompt(base_prompt, text, exc.message)

        # Unreachable: the loop above always either returns or raises on its
        # final iteration.
        raise last_error  # pragma: no cover

    def _call_with_retry(
        self,
        prompt: str,
        system: str | None,
        cfg: LLMConfig,
        response_schema: dict,
    ) -> str:
        last_error: LLMProviderError | None = None
        for attempt in range(1, cfg.max_attempts + 1):
            request = LLMRequest(
                prompt=prompt,
                system=system,
                model=cfg.model,
                temperature=cfg.temperature,
                timeout_seconds=cfg.timeout_seconds,
                response_schema=response_schema,
            )
            try:
                return self._provider.complete(request).text
            except LLMProviderError as exc:
                last_error = exc
                if not exc.retryable or attempt >= cfg.max_attempts:
                    raise
                if cfg.retry_backoff_seconds > 0:
                    time.sleep(cfg.retry_backoff_seconds * (2 ** (attempt - 1)))

        # Unreachable: max_attempts >= 1 guarantees either a return or a
        # raise inside the loop above.
        raise last_error  # pragma: no cover


__all__ = [
    "LLMClient",
    "extract_json_object",
    "parse_structured",
    "repair_prompt",
    "schema_instructions",
]
