"""Model and behavior configuration for the LLM Provider Layer.

`provider` selects which `LLMProvider` `LLMClient` builds by default: Groq
(primary — fast, free-tier, native JSON-schema structured output), Gemini
(alternative cloud provider), or Ollama (local alternative, local-first per
about_project.md's technology table). `from_env` resolves `model`/`api_key`
per-provider — see its own docstring — from the matching `GROQ_*`/
`GEMINI_*`/`OLLAMA_*` names declared in `.env.example`.

`LLMCallOptions` is the per-call override callers pass. It carries no
vendor-specific field, so selecting a model or temperature never drags a
provider-specific type into a domain component's signature.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, Field

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "llama3"
DEFAULT_PROVIDER = "groq"
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
"""`llama-3.3-70b-versatile` (an earlier default) turned out not to support
Groq's `json_schema` structured-output mode at all — every structured call
failed with a 400. Confirmed working via a direct API check against
console.groq.com/docs/structured-outputs#supported-models."""


class LLMCallOptions(BaseModel):
    """Per-call overrides. Any field left as None falls back to `LLMConfig`."""

    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    timeout_seconds: float | None = Field(default=None, gt=0.0)
    max_attempts: int | None = Field(default=None, ge=1)


class LLMConfig(BaseModel):
    """Layer-wide defaults, resolved once at construction."""

    provider: str = DEFAULT_PROVIDER
    """Which `LLMProvider` implementation `LLMClient` builds when none is
    injected: `"groq"` (default/primary — fast, free-tier, native
    JSON-schema support, see `groq_provider.py`), `"gemini"` (alternative
    cloud provider — see `gemini_provider.py`), or `"ollama"` (local
    alternative, local-first per about_project.md — see
    `ollama_provider.py`)."""

    host: str = DEFAULT_HOST
    model: str = DEFAULT_MODEL
    api_key: str | None = None
    """Vendor API key. Unused by `OllamaProvider`; required by `GroqProvider`/`GeminiProvider`."""

    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    """Defaults to 0.0: every current consumer does extraction, scoring, or
    structured generation, where reproducibility beats variety."""

    timeout_seconds: float = Field(default=120.0, gt=0.0)
    max_attempts: int = Field(default=3, ge=1)
    """Transport-level attempts on retryable failures. Matches the 3-attempt
    exponential-backoff policy in docs/architecture/kafka-topics.md, so a
    consumer's own retry budget isn't multiplied by a surprising one here."""

    retry_backoff_seconds: float = Field(default=0.5, ge=0.0)
    repair_attempts: int = Field(default=1, ge=0)
    """Extra re-prompts allowed when the response fails schema validation."""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LLMConfig:
        source = os.environ if env is None else env
        values: dict[str, object] = {}
        for field, key in _ENV_KEYS.items():
            raw = source.get(key)
            if raw is not None and raw != "":
                values[field] = raw

        # `model` and `api_key` are resolved separately, keyed off
        # `provider`: each vendor names models (and reads its key from a
        # different env var) differently, and e.g. `OLLAMA_MODEL` staying
        # set in `.env` while switching to `gemini`/`groq` shouldn't leak an
        # Ollama model name into that provider's call.
        provider = values.get("provider", DEFAULT_PROVIDER)
        if provider == "groq":
            values["model"] = source.get("GROQ_MODEL") or DEFAULT_GROQ_MODEL
            values["api_key"] = source.get("GROQ_API_KEY")
        elif provider == "gemini":
            values["model"] = source.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
            values["api_key"] = source.get("GEMINI_API_KEY")
        else:
            model = source.get("OLLAMA_MODEL")
            if model:
                values["model"] = model
            values.pop("api_key", None)

        return cls.model_validate(values)

    def merged(self, options: LLMCallOptions | None) -> LLMConfig:
        if options is None:
            return self
        overrides = options.model_dump(exclude_none=True)
        return self.model_copy(update=overrides)


_ENV_KEYS = {
    "provider": "LLM_PROVIDER",
    "host": "OLLAMA_HOST",
    "temperature": "LLM_TEMPERATURE",
    "timeout_seconds": "LLM_TIMEOUT_SECONDS",
    "max_attempts": "LLM_MAX_ATTEMPTS",
    "repair_attempts": "LLM_REPAIR_ATTEMPTS",
}


__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_GROQ_MODEL",
    "DEFAULT_HOST",
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "LLMCallOptions",
    "LLMConfig",
]
