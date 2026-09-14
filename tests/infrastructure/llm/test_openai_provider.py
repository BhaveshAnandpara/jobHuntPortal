"""Tests for OpenAIProvider (openai_provider.py) with the `openai` client
mocked via an injected `client`. No live OpenAI API is required or used
anywhere in this module — no request ever leaves the process.

Mirrors `test_groq_provider.py`'s shape deliberately: both providers wrap an
OpenAI-compatible chat-completions surface, so the same normalization
contract (vendor exception -> `LLMProviderError`, vendor response ->
`LLMResponse`) is asserted the same way.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
import pytest

from infrastructure.llm.config import DEFAULT_MODEL, LLMConfig
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from infrastructure.llm.openai_provider import (
    DEFAULT_OPENAI_MODEL,
    OpenAIProvider,
    _resolve_model,
    _schema_name,
)
from infrastructure.llm.provider import LLMRequest

MODEL = "gpt-4o-mini"


class FakeCompletions:
    """Stands in for `openai.OpenAI().chat.completions`. Records the kwargs
    it was called with and either returns a canned response or raises a
    canned exception."""

    def __init__(self, response=None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.last_call_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_call_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class FakeOpenAIClient:
    def __init__(self, response=None, error: Exception | None = None):
        self.chat = SimpleNamespace(completions=FakeCompletions(response=response, error=error))


def chat_completion(text: str, model: str = MODEL, prompt_tokens=12, completion_tokens=34):
    """The subset of `openai.types.chat.ChatCompletion` this provider reads:
    `choices[0].message.content`, `model`, `usage.{prompt,completion}_tokens`."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        model=model,
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
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


def make_provider(fake_client: FakeOpenAIClient, **config_overrides) -> OpenAIProvider:
    config_overrides.setdefault("api_key", "sk-test")
    config_overrides.setdefault("model", MODEL)
    return OpenAIProvider(config=LLMConfig(**config_overrides), client=fake_client)


def _connection_error(message="Connection error.") -> openai.APIConnectionError:
    return openai.APIConnectionError(message=message, request=httpx.Request("POST", "http://openai.test"))


def _timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=httpx.Request("POST", "http://openai.test"))


def _status_error(cls, status_code: int, message: str = "boom") -> openai.APIStatusError:
    response = httpx.Response(status_code, request=httpx.Request("POST", "http://openai.test"))
    return cls(message, response=response, body={"error": {"message": message}})


def test_complete_maps_request_fields_and_normalizes_response():
    fake_client = FakeOpenAIClient(response=chat_completion('{"name": "gizmo", "count": 3}'))
    provider = make_provider(fake_client)

    request = make_request(system="be terse", response_schema={"title": "Widget", "type": "object"})
    response = provider.complete(request)

    assert response.text == '{"name": "gizmo", "count": 3}'
    assert response.model == MODEL
    assert response.prompt_tokens == 12
    assert response.completion_tokens == 34

    sent = fake_client.chat.completions.last_call_kwargs
    assert sent["model"] == MODEL
    assert sent["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "describe the widget"},
    ]
    assert sent["temperature"] == 0.2
    assert sent["timeout"] == 15.0
    assert sent["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "Widget", "schema": {"title": "Widget", "type": "object"}},
    }


def test_complete_without_response_schema_omits_response_format():
    fake_client = FakeOpenAIClient(response=chat_completion("plain text"))
    provider = make_provider(fake_client)

    provider.complete(make_request(response_schema=None))

    assert "response_format" not in fake_client.chat.completions.last_call_kwargs


def test_complete_without_system_message_omits_it():
    fake_client = FakeOpenAIClient(response=chat_completion("hi"))
    provider = make_provider(fake_client)

    provider.complete(make_request(system=None))

    assert fake_client.chat.completions.last_call_kwargs["messages"] == [
        {"role": "user", "content": "describe the widget"}
    ]


def test_timeout_normalizes_to_timeout_reason():
    fake_client = FakeOpenAIClient(error=_timeout_error())
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    err = excinfo.value
    assert err.reason == LLMFailureReason.TIMEOUT
    assert err.provider == "openai"
    assert err.retryable is True


def test_connection_error_normalizes_to_connection_error_reason():
    fake_client = FakeOpenAIClient(error=_connection_error())
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.CONNECTION_ERROR


def test_rate_limit_normalizes_to_provider_error_and_stays_retryable():
    fake_client = FakeOpenAIClient(error=_status_error(openai.RateLimitError, 429, "slow down"))
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    err = excinfo.value
    assert err.reason == LLMFailureReason.PROVIDER_ERROR
    assert err.retryable is True
    assert "rate limited" in err.message


def test_authentication_error_normalizes_to_provider_error():
    fake_client = FakeOpenAIClient(error=_status_error(openai.AuthenticationError, 401, "bad key"))
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    err = excinfo.value
    assert err.reason == LLMFailureReason.PROVIDER_ERROR
    assert "401" in err.message


def test_bad_request_error_normalizes_to_provider_error():
    fake_client = FakeOpenAIClient(error=_status_error(openai.BadRequestError, 400, "unknown model"))
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.PROVIDER_ERROR
    assert "400" in excinfo.value.message


def test_unexpected_exception_is_never_leaked_raw():
    fake_client = FakeOpenAIClient(error=RuntimeError("something exotic broke"))
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.PROVIDER_ERROR
    assert "something exotic broke" in excinfo.value.message


def test_missing_message_content_normalizes_to_invalid_response():
    empty = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None))],
        model=MODEL,
        usage=None,
    )
    fake_client = FakeOpenAIClient(response=empty)
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.INVALID_RESPONSE


def test_empty_choices_normalizes_to_invalid_response():
    fake_client = FakeOpenAIClient(response=SimpleNamespace(choices=[], model=MODEL, usage=None))
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.INVALID_RESPONSE


def test_missing_usage_leaves_token_counts_unset():
    fake_client = FakeOpenAIClient(
        response=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            model=MODEL,
            usage=None,
        )
    )
    provider = make_provider(fake_client)

    response = provider.complete(make_request())

    assert response.prompt_tokens is None
    assert response.completion_tokens is None


def test_no_vendor_type_in_normalized_response():
    fake_client = FakeOpenAIClient(response=chat_completion("ok"))
    provider = make_provider(fake_client)

    response = provider.complete(make_request())

    assert type(response).__module__ == "infrastructure.llm.provider"


def test_missing_api_key_raises_before_any_request():
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIProvider(config=LLMConfig(provider="openai", model=MODEL, api_key=None))


def test_injected_client_does_not_require_an_api_key():
    """A caller that supplies its own client (as every test here does) never
    needs the key — the key only exists to build the default client."""
    provider = OpenAIProvider(
        config=LLMConfig(provider="openai", model=MODEL, api_key=None),
        client=FakeOpenAIClient(response=chat_completion("ok")),
    )

    assert provider.complete(make_request()).text == "ok"


def test_provider_name_is_openai():
    fake_client = FakeOpenAIClient(response=chat_completion("ok"))
    provider = make_provider(fake_client)
    assert provider.name == "openai"


def test_default_model_used_when_config_model_is_the_layer_wide_placeholder():
    """`LLMConfig.model` defaults to the layer-wide Ollama placeholder, which
    means nothing to OpenAI — the provider must substitute its own default
    rather than send it."""
    fake_client = FakeOpenAIClient(response=chat_completion("ok", model=DEFAULT_OPENAI_MODEL))
    provider = OpenAIProvider(
        config=LLMConfig(provider="openai", model=DEFAULT_MODEL, api_key="sk-test"),
        client=fake_client,
    )

    provider.complete(make_request(model=DEFAULT_MODEL))

    assert fake_client.chat.completions.last_call_kwargs["model"] == DEFAULT_OPENAI_MODEL


def test_explicit_model_override_is_sent_unchanged():
    fake_client = FakeOpenAIClient(response=chat_completion("ok", model="gpt-4o"))
    provider = make_provider(fake_client, model="gpt-4o")

    provider.complete(make_request(model="gpt-4o"))

    assert fake_client.chat.completions.last_call_kwargs["model"] == "gpt-4o"


def test_default_openai_model_constant():
    assert DEFAULT_OPENAI_MODEL == "gpt-4o-mini"


@pytest.mark.parametrize(
    "model,expected",
    [
        ("gpt-4o", "gpt-4o"),
        ("", DEFAULT_OPENAI_MODEL),
        (None, DEFAULT_OPENAI_MODEL),
        (DEFAULT_MODEL, DEFAULT_OPENAI_MODEL),
    ],
)
def test_resolve_model(model, expected):
    assert _resolve_model(model) == expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("ExtractedResumeProfile", "ExtractedResumeProfile"),
        (None, "response"),
        ("", "response"),
        ("Weird Title!!", "Weird_Title__"),
    ],
)
def test_schema_name_derivation(title, expected):
    schema = {"type": "object"} if title is None else {"title": title, "type": "object"}
    assert _schema_name(schema) == expected
