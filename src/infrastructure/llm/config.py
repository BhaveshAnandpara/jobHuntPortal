"""Model and behavior configuration for the LLM Provider Layer.

Defaults are local-first per about_project.md's technology table (Ollama,
"capable of running locally with minimal cost"), and `from_env` reads the
`OLLAMA_HOST` / `OLLAMA_MODEL` names already declared in `.env.example`.

`LLMCallOptions` is the per-call override callers pass. It carries no
vendor-specific field, so selecting a model or temperature never drags an
Ollama type into a domain component's signature.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, Field

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "llama3"
DEFAULT_PROVIDER = "ollama"
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"


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
    injected: `"ollama"` (default, local-first per about_project.md) or
    `"gemini"` (deployment-scoped override — see `gemini_provider.py`)."""

    host: str = DEFAULT_HOST
    model: str = DEFAULT_MODEL
    api_key: str | None = None
    """Vendor API key. Unused by `OllamaProvider`; required by `GeminiProvider`."""

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

        # `model` is resolved separately, keyed off `provider`: each vendor
        # names models differently, and `OLLAMA_MODEL` staying set in `.env`
        # while switching to `gemini` shouldn't leak an Ollama model name
        # into a Gemini call.
        provider = values.get("provider", DEFAULT_PROVIDER)
        if provider == "gemini":
            values["model"] = source.get("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
        else:
            model = source.get("OLLAMA_MODEL")
            if model:
                values["model"] = model

        return cls.model_validate(values)

    def merged(self, options: LLMCallOptions | None) -> LLMConfig:
        if options is None:
            return self
        overrides = options.model_dump(exclude_none=True)
        return self.model_copy(update=overrides)


_ENV_KEYS = {
    "provider": "LLM_PROVIDER",
    "host": "OLLAMA_HOST",
    "api_key": "GEMINI_API_KEY",
    "temperature": "LLM_TEMPERATURE",
    "timeout_seconds": "LLM_TIMEOUT_SECONDS",
    "max_attempts": "LLM_MAX_ATTEMPTS",
    "repair_attempts": "LLM_REPAIR_ATTEMPTS",
}


__all__ = ["DEFAULT_GEMINI_MODEL", "DEFAULT_HOST", "DEFAULT_MODEL", "DEFAULT_PROVIDER", "LLMCallOptions", "LLMConfig"]
