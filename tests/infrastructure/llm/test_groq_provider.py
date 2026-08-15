"""Tests for GroqProvider (groq_provider.py) with the `groq` client mocked
via an injected `client`. No live Groq server required or used anywhere in
this module.
"""

from __future__ import annotations

from types import SimpleNamespace

import groq
import httpx
import pytest

from infrastructure.llm.config import LLMConfig
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from infrastructure.llm.groq_provider import GroqProvider, _schema_name
from infrastructure.llm.provider import LLMRequest


class FakeCompletions:
    """Stands in for `groq.Groq().chat.completions`. Records the kwargs it
    was called with and either returns a canned response or raises a
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


class FakeGroqClient:
    def __init__(self, response=None, error: Exception | None = None):
        self.chat = SimpleNamespace(completions=FakeCompletions(response=response, error=error))


def chat_completion(text: str, model: str = "llama-3.3-70b-versatile", prompt_tokens=12, completion_tokens=34):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        model=model,
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def make_request(**overrides) -> LLMRequest:
    defaults = {
        "prompt": "describe the widget",
        "model": "llama-3.3-70b-versatile",
        "temperature": 0.2,
        "timeout_seconds": 15.0,
    }
    defaults.update(overrides)
    return LLMRequest(**defaults)


def make_provider(fake_client: FakeGroqClient, **config_overrides) -> GroqProvider:
    config_overrides.setdefault("api_key", "gsk_test")
    return GroqProvider(config=LLMConfig(**config_overrides), client=fake_client)


def _connection_error(message="Connection error.") -> groq.APIConnectionError:
    return groq.APIConnectionError(message=message, request=httpx.Request("POST", "http://groq.test"))


def _timeout_error() -> groq.APITimeoutError:
    return groq.APITimeoutError(request=httpx.Request("POST", "http://groq.test"))


def _status_error(cls, status_code: int, message: str = "boom") -> groq.APIStatusError:
    response = httpx.Response(status_code, request=httpx.Request("POST", "http://groq.test"))
    return cls(message, response=response, body={"error": {"message": message}})


def test_complete_maps_request_fields_and_normalizes_response():
    fake_client = FakeGroqClient(response=chat_completion('{"name": "gizmo", "count": 3}'))
    provider = make_provider(fake_client)

    request = make_request(system="be terse", response_schema={"title": "Widget", "type": "object"})
    response = provider.complete(request)

    assert response.text == '{"name": "gizmo", "count": 3}'
    assert response.model == "llama-3.3-70b-versatile"
    assert response.prompt_tokens == 12
    assert response.completion_tokens == 34

    sent = fake_client.chat.completions.last_call_kwargs
    assert sent["model"] == "llama-3.3-70b-versatile"
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
    fake_client = FakeGroqClient(response=chat_completion("plain text"))
    provider = make_provider(fake_client)

    provider.complete(make_request(response_schema=None))

    assert "response_format" not in fake_client.chat.completions.last_call_kwargs


def test_complete_without_system_message_omits_it():
    fake_client = FakeGroqClient(response=chat_completion("hi"))
    provider = make_provider(fake_client)

    provider.complete(make_request(system=None))

    assert fake_client.chat.completions.last_call_kwargs["messages"] == [
        {"role": "user", "content": "describe the widget"}
    ]


def test_timeout_normalizes_to_timeout_reason():
    fake_client = FakeGroqClient(error=_timeout_error())
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    err = excinfo.value
    assert err.reason == LLMFailureReason.TIMEOUT
    assert err.provider == "groq"
    assert err.retryable is True


def test_connection_error_normalizes_to_connection_error_reason():
    fake_client = FakeGroqClient(error=_connection_error())
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.CONNECTION_ERROR


def test_rate_limit_normalizes_to_provider_error_and_stays_retryable():
    fake_client = FakeGroqClient(error=_status_error(groq.RateLimitError, 429, "slow down"))
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    err = excinfo.value
    assert err.reason == LLMFailureReason.PROVIDER_ERROR
    assert err.retryable is True
    assert "rate limited" in err.message


def test_authentication_error_normalizes_to_provider_error():
    fake_client = FakeGroqClient(error=_status_error(groq.AuthenticationError, 401, "bad key"))
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    err = excinfo.value
    assert err.reason == LLMFailureReason.PROVIDER_ERROR
    assert "401" in err.message


def test_unexpected_exception_is_never_leaked_raw():
    fake_client = FakeGroqClient(error=RuntimeError("something exotic broke"))
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.PROVIDER_ERROR
    assert "something exotic broke" in excinfo.value.message


def test_missing_message_content_normalizes_to_invalid_response():
    empty = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None))],
        model="llama-3.3-70b-versatile",
        usage=None,
    )
    fake_client = FakeGroqClient(response=empty)
    provider = make_provider(fake_client)

    with pytest.raises(LLMProviderError) as excinfo:
        provider.complete(make_request())

    assert excinfo.value.reason == LLMFailureReason.INVALID_RESPONSE


def test_no_vendor_type_in_normalized_response():
    fake_client = FakeGroqClient(response=chat_completion("ok"))
    provider = make_provider(fake_client)

    response = provider.complete(make_request())

    assert type(response).__module__ == "infrastructure.llm.provider"


def test_missing_api_key_raises_before_any_request():
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        GroqProvider(config=LLMConfig(provider="groq", api_key=None))


def test_provider_name_is_groq():
    fake_client = FakeGroqClient(response=chat_completion("ok"))
    provider = make_provider(fake_client)
    assert provider.name == "groq"


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
