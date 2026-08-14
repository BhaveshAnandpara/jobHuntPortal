"""Tests for OllamaProvider (ollama_provider.py) with the `ollama` client
call mocked via an injected `client_factory`. No live Ollama server required
or used anywhere in this module.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import ollama
import pytest

from infrastructure.llm.config import LLMConfig
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from infrastructure.llm.ollama_provider import OllamaProvider
from infrastructure.llm.provider import LLMRequest


class FakeOllamaClient:
    """Stands in for `ollama.Client`. Records the kwargs it was called with
    and either returns a canned response or raises a canned exception."""

    def __init__(self, response=None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.last_call_kwargs: dict | None = None

    def chat(self, **kwargs):
        self.last_call_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


def make_request(**overrides) -> LLMRequest:
    defaults = {
        "prompt": "describe the widget",
        "model": "llama3",
        "temperature": 0.2,
        "timeout_seconds": 15.0,
    }
    defaults.update(overrides)
    return LLMRequest(**defaults)


def factory_for(fake_client: FakeOllamaClient):
    calls = []

    def factory(host, timeout_seconds):
        calls.append((host, timeout_seconds))
        return fake_client

    factory.calls = calls
    return factory


def test_complete_maps_request_fields_and_normalizes_response():
    raw = SimpleNamespace(
        message=SimpleNamespace(content='{"name": "gizmo", "count": 3}'),
        model="llama3",
        prompt_eval_count=12,
        eval_count=34,
    )
    fake_client = FakeOllamaClient(response=raw)
    factory = factory_for(fake_client)
    provider = OllamaProvider(config=LLMConfig(host="http://myhost:11434"), client_factory=factory)

    request = make_request(system="be terse", response_schema={"type": "object"})
    response = provider.complete(request)

    # Response normalized to the provider-agnostic LLMResponse shape only.
    assert response.text == '{"name": "gizmo", "count": 3}'
    assert response.model == "llama3"
    assert response.prompt_tokens == 12
    assert response.completion_tokens == 34

    # Request mapped correctly: system+user messages, model, temperature,
    # and the schema passed through as a native `format` constraint.
    sent = fake_client.last_call_kwargs
    assert sent["model"] == "llama3"
    assert sent["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "describe the widget"},
    ]
    assert sent["options"] == {"temperature": 0.2}
    assert sent["format"] == {"type": "object"}

    # Timeout/host resolved from LLMConfig.host and the per-request timeout.
    assert factory.calls == [("http://myhost:11434", 15.0)]


def test_complete_without_system_message_omits_it():
    raw = SimpleNamespace(message=SimpleNamespace(content="hi"), model="llama3")
    fake_client = FakeOllamaClient(response=raw)
    provider = OllamaProvider(config=LLMConfig(), client_factory=factory_for(fake_client))

    provider.complete(make_request(system=None))

    assert fake_client.last_call_kwargs["messages"] == [
        {"role": "user", "content": "describe the widget"}
    ]


def test_complete_accepts_dict_shaped_response():
    """Some test doubles / older SDK shapes return plain dicts rather than
    typed objects; the adapter must handle both."""
    raw = {"message": {"content": "plain dict content"}, "model": "llama3"}
    fake_client = FakeOllamaClient(response=raw)
    provider = OllamaProvider(config=LLMConfig(), client_factory=factory_for(fake_client))

    response = provider.complete(make_request())

    assert response.text == "plain dict content"
    assert response.model == "llama3"
    assert response.prompt_tokens is None
    assert response.completion_tokens is None


def test_connection_refused_normalizes_to_connection_error():
    fake_client = FakeOllamaClient(error=ConnectionError("Failed to connect to Ollama."))
    provider = OllamaProvider(config=LLMConfig(), client_factory=factory_for(fake_client))

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    err = excinfo.value
    assert err.reason == LLMFailureReason.CONNECTION_ERROR
    assert err.provider == "ollama"
    assert err.model == "llama3"
    assert err.retryable is True


def test_timeout_normalizes_to_timeout_reason():
    fake_client = FakeOllamaClient(error=httpx.ReadTimeout("timed out"))
    provider = OllamaProvider(config=LLMConfig(), client_factory=factory_for(fake_client))

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.TIMEOUT


def test_connect_timeout_also_normalizes_to_timeout_reason():
    fake_client = FakeOllamaClient(error=httpx.ConnectTimeout("connect timed out"))
    provider = OllamaProvider(config=LLMConfig(), client_factory=factory_for(fake_client))

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.TIMEOUT


def test_model_not_found_response_error_normalizes_to_provider_error():
    fake_client = FakeOllamaClient(error=ollama.ResponseError("model 'ghost' not found", 404))
    provider = OllamaProvider(config=LLMConfig(), client_factory=factory_for(fake_client))

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request(model="ghost"))

    err = excinfo.value
    assert err.reason == LLMFailureReason.PROVIDER_ERROR
    assert "404" in err.message


def test_request_error_normalizes_to_provider_error():
    fake_client = FakeOllamaClient(error=ollama.RequestError("invalid request"))
    provider = OllamaProvider(config=LLMConfig(), client_factory=factory_for(fake_client))

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.PROVIDER_ERROR


def test_missing_message_content_normalizes_to_invalid_response():
    raw = SimpleNamespace(message=SimpleNamespace(content=None), model="llama3")
    fake_client = FakeOllamaClient(response=raw)
    provider = OllamaProvider(config=LLMConfig(), client_factory=factory_for(fake_client))

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.INVALID_RESPONSE


def test_unexpected_exception_is_never_leaked_raw():
    """Safety net: any exception type the ollama SDK might raise that isn't
    explicitly handled still comes out as LLMProviderError, never the raw
    vendor/stdlib exception."""
    fake_client = FakeOllamaClient(error=RuntimeError("something exotic broke"))
    provider = OllamaProvider(config=LLMConfig(), client_factory=factory_for(fake_client))

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.PROVIDER_ERROR
    assert "something exotic broke" in excinfo.value.message


def test_no_vendor_type_in_normalized_response():
    raw = SimpleNamespace(message=SimpleNamespace(content="ok"), model="llama3")
    fake_client = FakeOllamaClient(response=raw)
    provider = OllamaProvider(config=LLMConfig(), client_factory=factory_for(fake_client))

    response = provider.complete(make_request())

    # LLMResponse is a plain pydantic model defined in provider.py; no
    # ollama.* type anywhere in the returned object graph.
    assert type(response).__module__ == "infrastructure.llm.provider"


def test_default_client_factory_builds_real_ollama_client(monkeypatch):
    """Construction path only — never performs a network call."""
    captured = {}

    class RecordingClient:
        def __init__(self, host=None, timeout=None):
            captured["host"] = host
            captured["timeout"] = timeout

    monkeypatch.setattr(ollama, "Client", RecordingClient)
    provider = OllamaProvider(config=LLMConfig(host="http://localhost:11434"))
    # Force use of the real (monkeypatched) default factory by not injecting one.
    from infrastructure.llm.ollama_provider import _default_client_factory

    client = _default_client_factory("http://localhost:11434", 5.0)

    assert isinstance(client, RecordingClient)
    assert captured == {"host": "http://localhost:11434", "timeout": 5.0}
    assert provider.name == "ollama"
