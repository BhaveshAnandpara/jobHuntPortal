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
whichever `LLMProvider` `LLMConfig.from_env()`'s `provider` field selects —
`"groq"` (default/primary), `"gemini"`, or `"ollama"` (local-first fallback
per about_project.md) — see `_default_provider()`.
"""

from __future__ import annotations

import json
import time
from types import UnionType
from typing import TypeVar, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from infrastructure.llm.config import LLMCallOptions, LLMConfig
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError, excerpt
from infrastructure.llm.provider import LLMProvider, LLMRequest
from infrastructure.logging import get_logger

T = TypeVar("T", bound=BaseModel)

logger = get_logger(__name__)


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


def _is_list_annotation(annotation: object) -> bool:
    """True for `list[...]` and for a union that includes `list[...]`
    (covers both `list[str]` and `list[str] | None`)."""
    origin = get_origin(annotation)
    if origin is list:
        return True
    if origin is UnionType or origin is Union:
        return any(_is_list_annotation(arg) for arg in get_args(annotation))
    return False


def _unwrap_schema_wrapper(parsed: dict, schema: type[BaseModel]) -> dict:
    """Recover from a model wrapping its answer in one extra top-level key
    instead of returning the bare object — e.g. `{"ExtractedResumeProfile":
    {...}}`, `{"profile": {...}}`, `{"result": {...}}`, `{"data": {...}}` —
    even when the prompt explicitly says not to. Small local models (e.g.
    `llama3.2:1b`) do this often enough that it needs handling here, at the
    shared JSON-to-schema seam every structured call passes through, rather
    than as a resume-specific special case.

    Only unwraps when it's unambiguous: exactly one top-level key, whose
    value is itself a dict, where the *outer* key is not a field on `schema`
    but the *inner* dict shares at least one field name with `schema`. That
    keeps a legitimately-shaped response (whose one populated field happens
    to be a dict) from being misread as a wrapper.
    """
    if len(parsed) != 1:
        return parsed
    ((key, value),) = parsed.items()
    if not isinstance(value, dict):
        return parsed
    schema_fields = schema.model_fields.keys()
    if key in schema_fields:
        return parsed
    if not (value.keys() & schema_fields):
        return parsed
    return value


def _looks_like_schema_echo(parsed: dict) -> bool:
    """True if `parsed` is the *JSON Schema definition* itself rather than
    data conforming to it — e.g. a small model asked to "return an object
    matching this JSON Schema: {...}" sometimes echoes that schema object
    back verbatim instead of producing an instance of it.

    This is easy to miss downstream: `model_json_schema()` always includes
    a top-level `"title"` set to the class name, so an echoed schema for
    `ExtractedResumeProfile` validates as data with `title=
    "ExtractedResumeProfile"` — syntactically valid, semantically garbage,
    and Pydantic never raises on it. Detected structurally (`"properties"`
    + `"type": "object"` at the top level) rather than by name, so it
    applies to any schema this layer is asked to fill in, not just resumes:
    no real extraction schema in this codebase has fields literally named
    `properties`/`type`, so this can't misfire on genuine data.
    """
    return parsed.get("type") == "object" and isinstance(parsed.get("properties"), dict)


def _coerce_null_lists(parsed: dict, schema: type[BaseModel]) -> None:
    """Rewrite an explicit JSON `null` to `[]` for any list-typed field,
    in place.

    Small local models routinely emit `null` instead of `[]` for a
    list-typed field it has nothing to report for (e.g. "no skills found"),
    even when told to return an empty list — this is a response-shape quirk
    of the model, not a meaningful "no value" the caller's schema should
    have to special-case. Pydantic only applies a field's default when the
    key is *absent*; an explicit `null` for a non-Optional `list[str]`
    field fails validation instead of falling back to the default, so every
    structured-output schema in this codebase (`ExtractedJobFields`,
    `ProfileScoringOutput`, `ExtractedResumeProfile`, ...) was equally
    exposed to this — hence the fix living once here, at the shared
    JSON-to-schema seam every structured LLM call passes through, rather
    than in each schema.
    """
    for name, field in schema.model_fields.items():
        if parsed.get(name, ...) is None and _is_list_annotation(field.annotation):
            parsed[name] = []


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

    if isinstance(parsed, dict) and _looks_like_schema_echo(parsed):
        raise LLMProviderError(
            LLMFailureReason.INVALID_RESPONSE,
            "response was the JSON Schema definition, not data matching it",
            provider=provider,
            model=model,
            attempts=attempts,
            raw_text=text,
        )

    if isinstance(parsed, dict):
        parsed = _unwrap_schema_wrapper(parsed, schema)
        _coerce_null_lists(parsed, schema)

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
    # Imported lazily to avoid a module-load-time dependency on the
    # `groq`/`google-genai`/`ollama` SDKs for callers that always inject
    # their own provider (e.g. tests).
    if config.provider == "groq":
        from infrastructure.llm.groq_provider import GroqProvider

        return GroqProvider(config=config)

    if config.provider == "gemini":
        from infrastructure.llm.gemini_provider import GeminiProvider

        return GeminiProvider(config=config)

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
        """Run prompt and return a validated schema instance.

        Two independent retry mechanisms exist:

        1. Transport retries:
        Handled by `_call_with_retry`.
        Used for retryable provider/network failures such as timeout,
        connection errors, and HTTP 429.

        2. Structured-output repair:
        Handled here.
        Used only when the provider successfully responds but the response
        cannot be validated against the requested schema.

        Transport retries do not consume the structured-output repair budget.
        """

        cfg = self._config.merged(options)
        response_schema = schema.model_json_schema()

        base_prompt = (
            f"{prompt}\n\n"
            f"{schema_instructions(schema)}"
        )

        current_prompt = base_prompt
        last_error: LLMProviderError | None = None

        total_repair_rounds = cfg.repair_attempts + 1

        for repair_round in range(1, total_repair_rounds + 1):
            logger.info(
                "Structured LLM request | provider=%s model=%s "
                "repair_round=%d/%d",
                self._provider.name,
                cfg.model,
                repair_round,
                total_repair_rounds,
            )

            # Any transport retries happen internally here.
            text = self._call_with_retry(
                current_prompt,
                system,
                cfg,
                response_schema,
            )

            try:
                return parse_structured(
                    text,
                    schema,
                    provider=self._provider.name,
                    model=cfg.model,
                    attempts=repair_round,
                )

            except LLMProviderError as exc:
                last_error = exc

                logger.warning(
                    "Structured output validation failed | "
                    "provider=%s model=%s repair_round=%d/%d "
                    "error_code=%s",
                    self._provider.name,
                    cfg.model,
                    repair_round,
                    total_repair_rounds,
                    exc.error_code.value,
                )

                if repair_round >= total_repair_rounds:
                    raise

                current_prompt = repair_prompt(
                    base_prompt,
                    text,
                    exc.message,
                )

        # Defensive only — the loop always returns or raises.
        raise last_error  # pragma: no cover


    def _call_with_retry(
        self,
        prompt: str,
        system: str | None,
        cfg: LLMConfig,
        response_schema: dict,
    ) -> str:
        last_error: LLMProviderError | None = None

        for transport_attempt in range(1, cfg.max_attempts + 1):
            request = LLMRequest(
                prompt=prompt,
                system=system,
                model=cfg.model,
                temperature=cfg.temperature,
                timeout_seconds=cfg.timeout_seconds,
                response_schema=response_schema,
            )

            try:
                logger.info(
                    "LLM request started | provider=%s model=%s "
                    "transport_attempt=%d/%d",
                    self._provider.name,
                    cfg.model,
                    transport_attempt,
                    cfg.max_attempts,
                )

                return self._provider.complete(request).text

            except LLMProviderError as exc:
                last_error = exc

                logger.warning(
                    "LLM request failed | provider=%s model=%s "
                    "transport_attempt=%d/%d retryable=%s "
                    "retry_after_seconds=%s error_code=%s",
                    self._provider.name,
                    cfg.model,
                    transport_attempt,
                    cfg.max_attempts,
                    exc.retryable,
                    cfg.retry_backoff_seconds,
                    exc.error_code.value,
                )

                # Permanent error or retry budget exhausted.
                if not exc.retryable or transport_attempt >= cfg.max_attempts:
                    raise

                sleep_seconds = cfg.retry_backoff_seconds * (2 ** (transport_attempt - 1))

                if sleep_seconds > 0:
                    logger.info(
                        "LLM retry scheduled | provider=%s model=%s "
                        "transport_attempt=%d/%d wait_seconds=%.2f",
                        self._provider.name,
                        cfg.model,
                        transport_attempt,
                        cfg.max_attempts,
                        sleep_seconds,
                    )

                    time.sleep(sleep_seconds)

        # Defensive only — the loop always returns or raises.
        raise last_error  # pragma: no cover

__all__ = [
    "LLMClient",
    "extract_json_object",
    "parse_structured",
    "repair_prompt",
    "schema_instructions",
]
