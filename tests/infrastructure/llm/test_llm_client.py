"""Tests for LLMClient (structured.py) using FakeProvider.

These are the primary tests for this layer: deterministic, no network,
covering the retry (transport-level) and repair (structured-output-level)
budgets separately, per LLMConfig.max_attempts / repair_attempts /
retry_backoff_seconds, and confirming errors propagate as LLMProviderError
without ever leaking a provider-specific type.
"""

from __future__ import annotations

import pytest

from infrastructure.llm.config import LLMCallOptions, LLMConfig
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from infrastructure.llm.structured import LLMClient
from tests.infrastructure.llm.conftest import (
    FakeProvider,
    Widget,
    malformed_response,
    schema_mismatch_response,
    widget_response,
)


def fast_config(**overrides) -> LLMConfig:
    """LLMConfig with zero backoff so retry tests never sleep."""
    defaults = {"retry_backoff_seconds": 0.0}
    defaults.update(overrides)
    return LLMConfig(**defaults)


def test_complete_structured_success_first_try():
    provider = FakeProvider([widget_response("gizmo", 3)])
    client = LLMClient(provider=provider, config=fast_config())

    result = client.complete_structured("describe the widget", Widget)

    assert result == Widget(name="gizmo", count=3)
    assert provider.call_count == 1


def test_complete_structured_sends_resolved_request_fields():
    provider = FakeProvider([widget_response()])
    cfg = fast_config(model="mistral", temperature=0.5, timeout_seconds=42.0)
    client = LLMClient(provider=provider, config=cfg)

    client.complete_structured("prompt text", Widget, system="be terse")

    sent = provider.calls[0]
    assert sent.model == "mistral"
    assert sent.temperature == 0.5
    assert sent.timeout_seconds == 42.0
    assert sent.system == "be terse"
    assert "prompt text" in sent.prompt
    # Native structured-output constraint passed through to the provider.
    assert sent.response_schema == Widget.model_json_schema()


def test_complete_structured_per_call_options_override_config():
    provider = FakeProvider([widget_response()])
    client = LLMClient(provider=provider, config=fast_config(model="llama3", temperature=0.0))

    client.complete_structured(
        "prompt", Widget, options=LLMCallOptions(model="phi3", temperature=1.0)
    )

    sent = provider.calls[0]
    assert sent.model == "phi3"
    assert sent.temperature == 1.0


def test_repair_prompt_used_after_malformed_response():
    provider = FakeProvider([malformed_response(), widget_response("fixed", 7)])
    client = LLMClient(provider=provider, config=fast_config(repair_attempts=1))

    result = client.complete_structured("prompt", Widget)

    assert result == Widget(name="fixed", count=7)
    assert provider.call_count == 2
    # Second call's prompt is a repair prompt referencing the failed attempt.
    assert "Previous response" in provider.calls[1].prompt
    assert "corrected JSON object" in provider.calls[1].prompt


def test_repair_prompt_used_after_schema_mismatch():
    provider = FakeProvider([schema_mismatch_response(), widget_response("ok", 1)])
    client = LLMClient(provider=provider, config=fast_config(repair_attempts=1))

    result = client.complete_structured("prompt", Widget)

    assert result == Widget(name="ok", count=1)
    assert provider.call_count == 2


def test_repair_budget_exhausted_raises_normalized_error():
    provider = FakeProvider([malformed_response(), malformed_response()])
    client = LLMClient(provider=provider, config=fast_config(repair_attempts=1))

    with pytest.raises(LLMProviderError) as excinfo:
        client.complete_structured("prompt", Widget)

    assert excinfo.value.reason == LLMFailureReason.INVALID_RESPONSE
    assert provider.call_count == 2  # 1 initial + 1 repair, then gives up


def test_zero_repair_attempts_fails_on_first_bad_response():
    provider = FakeProvider([malformed_response()])
    client = LLMClient(provider=provider, config=fast_config(repair_attempts=0))

    with pytest.raises(LLMProviderError):
        client.complete_structured("prompt", Widget)

    assert provider.call_count == 1


def test_transport_error_is_retried_up_to_max_attempts():
    err = LLMProviderError(
        LLMFailureReason.CONNECTION_ERROR, "boom", provider="fake", model="llama3"
    )
    provider = FakeProvider([err, err, widget_response("survived", 9)])
    client = LLMClient(provider=provider, config=fast_config(max_attempts=3))

    result = client.complete_structured("prompt", Widget)

    assert result == Widget(name="survived", count=9)
    assert provider.call_count == 3


def test_transport_retries_exhausted_propagates_error_without_repair():
    err = LLMProviderError(
        LLMFailureReason.TIMEOUT, "timed out", provider="fake", model="llama3"
    )
    provider = FakeProvider([err, err, err])
    client = LLMClient(provider=provider, config=fast_config(max_attempts=3, repair_attempts=2))

    with pytest.raises(LLMProviderError) as excinfo:
        client.complete_structured("prompt", Widget)

    assert excinfo.value.reason == LLMFailureReason.TIMEOUT
    # All 3 attempts consumed by the transport retry budget; repair rounds
    # are for structured-output failures only and never triggered here.
    assert provider.call_count == 3


def test_non_retryable_transport_error_is_not_retried():
    # SCHEMA_VALIDATION_FAILED is not in errors.py's retryable set, even
    # though it's being raised here directly by provider.complete() rather
    # than by parse_structured() — proves the client honors
    # LLMFailureReason.retryable rather than blindly retrying every
    # LLMProviderError it sees.
    non_retryable = LLMProviderError(
        LLMFailureReason.SCHEMA_VALIDATION_FAILED,
        "schema mismatch raised directly by provider.complete",
        provider="fake",
        model="llama3",
    )
    provider = FakeProvider([non_retryable, widget_response()])
    client = LLMClient(provider=provider, config=fast_config(max_attempts=5))

    with pytest.raises(LLMProviderError):
        client.complete_structured("prompt", Widget)

    # Only the first (failing) call was made — no blind retry.
    assert provider.call_count == 1


def test_default_provider_is_groq_when_none_injected():
    """Groq is the primary/default provider — `LLMConfig.provider` defaults
    to `"groq"`, so a caller that injects neither a provider nor an
    explicit `provider=` gets `GroqProvider`."""
    from infrastructure.llm.groq_provider import GroqProvider

    client = LLMClient(config=fast_config(api_key="gsk_test"))
    assert isinstance(client.provider, GroqProvider)


def test_default_provider_is_gemini_when_configured():
    from infrastructure.llm.gemini_provider import GeminiProvider

    client = LLMClient(config=fast_config(provider="gemini", api_key="gem_test"))
    assert isinstance(client.provider, GeminiProvider)


def test_default_provider_is_ollama_when_configured():
    from infrastructure.llm.ollama_provider import OllamaProvider

    client = LLMClient(config=fast_config(provider="ollama"))
    assert isinstance(client.provider, OllamaProvider)


def test_injected_provider_is_used_as_is():
    provider = FakeProvider([widget_response()])
    client = LLMClient(provider=provider, config=fast_config())
    assert client.provider is provider
