"""Tests for AnthropicProvider (anthropic_provider.py) with the `anthropic`
client mocked via an injected `client`. No live Anthropic API call is made
or required anywhere in this module.

Mirrors `test_groq_provider.py`'s shape: a fake client records the kwargs it
was handed and either returns a canned response object or raises a canned
vendor exception, so every branch of the normalization is exercised without
the network.
"""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from infrastructure.llm.anthropic_provider import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_MAX_TOKENS,
    AnthropicProvider,
)
from infrastructure.llm.config import DEFAULT_MODEL, LLMConfig
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from infrastructure.llm.provider import LLMRequest

MODEL = "claude-sonnet-4-5"


class FakeMessages:
    """Stands in for `anthropic.Anthropic().messages`."""

    def __init__(self, response=None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.last_call_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_call_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class FakeAnthropicClient:
    def __init__(self, response=None, error: Exception | None = None):
        self.messages = FakeMessages(response=response, error=error)


def text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def message(text: str | list, model: str = MODEL, input_tokens=12, output_tokens=34, stop_reason="end_turn"):
    """A stand-in for the `anthropic.types.Message` returned by
    `messages.create`: `content` is a *list of blocks*, not a string."""
    content = [text_block(text)] if isinstance(text, str) else text
    return SimpleNamespace(
        content=content,
        model=model,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def make_request(**overrides) -> LLMRequest:
    defaults = {
        "prompt": "describe the widget",
        "model": MODEL,
        "temperature": 0.2,
        "timeout_seconds": 15.0,
    }
    defaults.update(overrides)
    return LLMRequest(**defaults)


def make_provider(fake_client: FakeAnthropicClient, **config_overrides) -> AnthropicProvider:
    config_overrides.setdefault("api_key", "sk-ant-test")
    config_overrides.setdefault("model", MODEL)
    return AnthropicProvider(config=LLMConfig(**config_overrides), client=fake_client)


def _connection_error(message_text="Connection error.") -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(
        message=message_text, request=httpx2.Request("POST", "http://anthropic.test")
    )


def _timeout_error() -> anthropic.APITimeoutError:
    return anthropic.APITimeoutError(request=httpx2.Request("POST", "http://anthropic.test"))


def _status_error(cls, status_code: int, message_text: str = "boom") -> anthropic.APIStatusError:
    response = httpx2.Response(status_code, request=httpx2.Request("POST", "http://anthropic.test"))
    return cls(message_text, response=response, body={"error": {"message": message_text}})


def test_complete_maps_request_fields_and_normalizes_response():
    fake_client = FakeAnthropicClient(response=message('{"name": "gizmo", "count": 3}'))
    provider = make_provider(fake_client)

    request = make_request(system="be terse", response_schema={"title": "Widget", "type": "object"})
    response = provider.complete(request)

    assert response.text == '{"name": "gizmo", "count": 3}'
    assert response.model == MODEL
    assert response.prompt_tokens == 12
    assert response.completion_tokens == 34

    sent = fake_client.messages.last_call_kwargs
    assert sent["model"] == MODEL
    assert sent["messages"] == [{"role": "user", "content": "describe the widget"}]
    assert sent["system"] == "be terse"
    assert sent["max_tokens"] == DEFAULT_MAX_TOKENS
    assert sent["timeout"] == 15.0


def test_complete_without_system_omits_the_system_parameter():
    fake_client = FakeAnthropicClient(response=message("hi"))
    provider = make_provider(fake_client)

    provider.complete(make_request(system=None))

    assert "system" not in fake_client.messages.last_call_kwargs


def test_temperature_and_response_schema_are_not_sent():
    """`anthropic` 1.x's Messages API exposes no `temperature` parameter, and
    this provider deliberately ignores `response_schema` (the protocol allows
    it) because Pydantic-generated schemas don't meet Anthropic's structured
    output subset. Passing either would 400 against the real API."""
    fake_client = FakeAnthropicClient(response=message("ok"))
    provider = make_provider(fake_client)

    provider.complete(make_request(temperature=0.7, response_schema={"title": "Widget", "type": "object"}))

    sent = fake_client.messages.last_call_kwargs
    assert "temperature" not in sent
    assert "response_format" not in sent
    assert "output_config" not in sent


def test_multiple_text_blocks_are_joined():
    fake_client = FakeAnthropicClient(response=message([text_block('{"name": '), text_block('"gizmo"}')]))
    provider = make_provider(fake_client)

    assert provider.complete(make_request()).text == '{"name": "gizmo"}'


def test_non_text_blocks_are_skipped():
    """A response may carry `thinking`/`tool_use` blocks with no usable
    `.text`; only `text` blocks contribute to `LLMResponse.text`."""
    blocks = [
        SimpleNamespace(type="thinking", thinking="hmm"),
        text_block("the answer"),
        SimpleNamespace(type="tool_use", id="t1", name="calc", input={}),
    ]
    fake_client = FakeAnthropicClient(response=message(blocks))
    provider = make_provider(fake_client)

    assert provider.complete(make_request()).text == "the answer"


def test_timeout_normalizes_to_timeout_reason():
    fake_client = FakeAnthropicClient(error=_timeout_error())
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    err = excinfo.value
    assert err.reason == LLMFailureReason.TIMEOUT
    assert err.provider == "claude"
    assert err.retryable is True


def test_connection_error_normalizes_to_connection_error_reason():
    fake_client = FakeAnthropicClient(error=_connection_error())
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.CONNECTION_ERROR


def test_rate_limit_normalizes_to_provider_error_and_stays_retryable():
    fake_client = FakeAnthropicClient(error=_status_error(anthropic.RateLimitError, 429, "slow down"))
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    err = excinfo.value
    assert err.reason == LLMFailureReason.PROVIDER_ERROR
    assert err.retryable is True
    assert "rate limited" in err.message


def test_authentication_error_normalizes_to_provider_error():
    fake_client = FakeAnthropicClient(error=_status_error(anthropic.AuthenticationError, 401, "bad key"))
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    err = excinfo.value
    assert err.reason == LLMFailureReason.PROVIDER_ERROR
    assert "401" in err.message


def test_bad_request_error_normalizes_to_provider_error():
    fake_client = FakeAnthropicClient(error=_status_error(anthropic.BadRequestError, 400, "bad model"))
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.PROVIDER_ERROR
    assert "400" in excinfo.value.message


def test_unexpected_exception_is_never_leaked_raw():
    fake_client = FakeAnthropicClient(error=RuntimeError("something exotic broke"))
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.PROVIDER_ERROR
    assert "something exotic broke" in excinfo.value.message


def test_empty_content_normalizes_to_invalid_response():
    fake_client = FakeAnthropicClient(response=message([]))
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.INVALID_RESPONSE


def test_refusal_is_reported_as_invalid_response_with_a_diagnosable_message():
    """A safety refusal is an HTTP 200 with `stop_reason="refusal"` and no
    usable text — it never reaches an except branch."""
    fake_client = FakeAnthropicClient(response=message([], stop_reason="refusal"))
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    err = excinfo.value
    assert err.reason == LLMFailureReason.INVALID_RESPONSE
    assert "refusal" in err.message


def test_missing_usage_leaves_token_counts_none():
    response = SimpleNamespace(content=[text_block("ok")], model=MODEL, stop_reason="end_turn", usage=None)
    fake_client = FakeAnthropicClient(response=response)
    provider = make_provider(fake_client)

    normalized = provider.complete(make_request())

    assert normalized.prompt_tokens is None
    assert normalized.completion_tokens is None


def test_no_vendor_type_in_normalized_response():
    fake_client = FakeAnthropicClient(response=message("ok"))
    provider = make_provider(fake_client)

    response = provider.complete(make_request())

    assert type(response).__module__ == "infrastructure.llm.provider"


def test_missing_api_key_raises_before_any_request():
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider(config=LLMConfig(provider="claude", api_key=None))


def test_provider_name_is_claude():
    fake_client = FakeAnthropicClient(response=message("ok"))
    assert make_provider(fake_client).name == "claude"


def test_default_model_is_used_when_no_override_is_configured():
    """`LLMConfig.model` falls back to the layer-wide generic `DEFAULT_MODEL`
    (an Ollama model name) when nothing Claude-specific was resolved; that
    must not be sent to Anthropic."""
    fake_client = FakeAnthropicClient(response=message("ok"))
    provider = AnthropicProvider(
        config=LLMConfig(provider="claude", api_key="sk-ant-test", model=DEFAULT_MODEL),
        client=fake_client,
    )

    provider.complete(make_request(model=DEFAULT_MODEL))

    assert fake_client.messages.last_call_kwargs["model"] == DEFAULT_ANTHROPIC_MODEL
    assert DEFAULT_ANTHROPIC_MODEL == "claude-sonnet-5"


def test_explicit_model_override_is_passed_through():
    fake_client = FakeAnthropicClient(response=message("ok", model="claude-opus-4-5"))
    provider = make_provider(fake_client)

    response = provider.complete(make_request(model="claude-opus-4-5"))

    assert fake_client.messages.last_call_kwargs["model"] == "claude-opus-4-5"
    assert response.model == "claude-opus-4-5"


def test_error_carries_the_resolved_model_not_the_placeholder():
    fake_client = FakeAnthropicClient(error=_timeout_error())
    provider = AnthropicProvider(
        config=LLMConfig(provider="claude", api_key="sk-ant-test", model=DEFAULT_MODEL),
        client=fake_client,
    )

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request(model=DEFAULT_MODEL))

    assert excinfo.value.model == DEFAULT_ANTHROPIC_MODEL


def test_satisfies_the_llm_provider_protocol():
    from infrastructure.llm.provider import LLMProvider

    assert isinstance(make_provider(FakeAnthropicClient(response=message("ok"))), LLMProvider)
