"""Tests for LLMConfig/LLMCallOptions (config.py)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from infrastructure.llm.config import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GROQ_MODEL,
    DEFAULT_HOST,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    LLMCallOptions,
    LLMConfig,
)


def test_defaults_are_ollama_shaped_when_constructed_directly():
    """Bare construction (no `from_env`) keeps the original Ollama-flavored
    field defaults — existing direct-construction call sites (tests
    building `OllamaProvider(config=LLMConfig(...))`) depend on this. Only
    `from_env()`'s resolution is provider-aware (see its own tests below);
    `provider` itself still defaults to the primary provider either way."""
    cfg = LLMConfig()
    assert cfg.provider == DEFAULT_PROVIDER == "groq"
    assert cfg.host == DEFAULT_HOST == "http://localhost:11434"
    assert cfg.model == DEFAULT_MODEL == "llama3"
    assert cfg.temperature == 0.0
    assert cfg.timeout_seconds == 120.0
    assert cfg.max_attempts == 3
    assert cfg.retry_backoff_seconds == 0.5
    assert cfg.repair_attempts == 1


def test_from_env_reads_expected_keys_for_ollama():
    env = {
        "LLM_PROVIDER": "ollama",
        "OLLAMA_HOST": "http://ollama-box:11434",
        "OLLAMA_MODEL": "mistral",
        "LLM_TEMPERATURE": "0.7",
        "LLM_TIMEOUT_SECONDS": "30",
        "LLM_MAX_ATTEMPTS": "5",
        "LLM_REPAIR_ATTEMPTS": "2",
    }
    cfg = LLMConfig.from_env(env)
    assert cfg.host == "http://ollama-box:11434"
    assert cfg.model == "mistral"
    assert cfg.api_key is None
    assert cfg.temperature == 0.7
    assert cfg.timeout_seconds == 30.0
    assert cfg.max_attempts == 5
    assert cfg.repair_attempts == 2


def test_from_env_defaults_to_groq_when_provider_unset():
    """`LLM_PROVIDER` unset resolves to the primary provider (Groq), with
    its own default model rather than Ollama's — unlike bare `LLMConfig()`
    construction, `from_env()` always resolves a provider-appropriate
    model."""
    cfg = LLMConfig.from_env({})
    assert cfg.provider == "groq"
    assert cfg.model == DEFAULT_GROQ_MODEL
    assert cfg.api_key is None


def test_from_env_resolves_groq_model_and_key():
    cfg = LLMConfig.from_env(
        {"LLM_PROVIDER": "groq", "GROQ_API_KEY": "gsk_test", "GROQ_MODEL": "llama-3.1-8b-instant"}
    )
    assert cfg.provider == "groq"
    assert cfg.model == "llama-3.1-8b-instant"
    assert cfg.api_key == "gsk_test"


def test_from_env_groq_falls_back_to_default_model_when_unset():
    cfg = LLMConfig.from_env({"LLM_PROVIDER": "groq", "GROQ_API_KEY": "gsk_test"})
    assert cfg.model == DEFAULT_GROQ_MODEL


def test_from_env_resolves_gemini_model_and_key():
    cfg = LLMConfig.from_env(
        {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "gem_test", "GEMINI_MODEL": "gemini-2.5-flash"}
    )
    assert cfg.provider == "gemini"
    assert cfg.model == "gemini-2.5-flash"
    assert cfg.api_key == "gem_test"


def test_from_env_stale_ollama_model_does_not_leak_into_other_providers():
    """Regression coverage: a `.env` that still has `OLLAMA_MODEL` set from
    a prior local-Ollama setup must not leak that model name into a
    Groq/Gemini call once `LLM_PROVIDER` switches away from Ollama — this
    exact bug previously broke job ingestion (jobs/ingestion/dependencies.py
    pinned an Ollama model name via a per-call override that ignored the
    active provider)."""
    cfg = LLMConfig.from_env(
        {"LLM_PROVIDER": "groq", "GROQ_API_KEY": "gsk_test", "OLLAMA_MODEL": "llama3.2:3b"}
    )
    assert cfg.model == DEFAULT_GROQ_MODEL
    assert cfg.model != "llama3.2:3b"


def test_from_env_ignores_empty_string_values():
    cfg = LLMConfig.from_env({"LLM_PROVIDER": "ollama", "OLLAMA_MODEL": "", "OLLAMA_HOST": "http://x:11434"})
    assert cfg.model == DEFAULT_MODEL
    assert cfg.host == "http://x:11434"


def test_merged_with_none_options_returns_self():
    cfg = LLMConfig()
    assert cfg.merged(None) is cfg


def test_merged_overrides_only_set_fields():
    cfg = LLMConfig(model="llama3", temperature=0.0, timeout_seconds=120.0)
    options = LLMCallOptions(temperature=0.9)
    merged = cfg.merged(options)

    assert merged.temperature == 0.9
    assert merged.model == "llama3"  # untouched
    assert merged.timeout_seconds == 120.0  # untouched
    assert cfg.temperature == 0.0  # original config unmodified (immutable merge)


def test_merged_can_override_multiple_fields():
    cfg = LLMConfig()
    options = LLMCallOptions(model="mistral", max_attempts=1)
    merged = cfg.merged(options)
    assert merged.model == "mistral"
    assert merged.max_attempts == 1
    assert merged.temperature == cfg.temperature


@pytest.mark.parametrize(
    "field,value",
    [
        ("temperature", -0.1),
        ("temperature", 2.1),
        ("timeout_seconds", 0.0),
        ("max_attempts", 0),
    ],
)
def test_invalid_field_values_are_rejected(field, value):
    with pytest.raises(ValidationError):
        LLMConfig(**{field: value})
