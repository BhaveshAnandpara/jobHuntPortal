"""Tests for LLMConfig/LLMCallOptions (config.py)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from infrastructure.llm.config import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    LLMCallOptions,
    LLMConfig,
)


def test_defaults_are_local_first():
    cfg = LLMConfig()
    assert cfg.host == DEFAULT_HOST == "http://localhost:11434"
    assert cfg.model == DEFAULT_MODEL == "llama3"
    assert cfg.temperature == 0.0
    assert cfg.timeout_seconds == 120.0
    assert cfg.max_attempts == 3
    assert cfg.retry_backoff_seconds == 0.5
    assert cfg.repair_attempts == 1


def test_from_env_reads_expected_keys():
    env = {
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
    assert cfg.temperature == 0.7
    assert cfg.timeout_seconds == 30.0
    assert cfg.max_attempts == 5
    assert cfg.repair_attempts == 2


def test_from_env_empty_mapping_uses_defaults():
    cfg = LLMConfig.from_env({})
    assert cfg == LLMConfig()


def test_from_env_ignores_empty_string_values():
    cfg = LLMConfig.from_env({"OLLAMA_MODEL": "", "OLLAMA_HOST": "http://x:11434"})
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
