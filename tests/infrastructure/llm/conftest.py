"""Shared fixtures/test doubles for the LLM Provider Layer test suite.

`FakeProvider` is a deterministic, in-memory stand-in for a real
`LLMProvider` implementation (structurally satisfies the Protocol in
provider.py). It never touches the network, so `LLMClient` tests exercise
retry/repair logic without a live Ollama server.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from infrastructure.llm.provider import LLMRequest, LLMResponse


class Widget(BaseModel):
    """A minimal target schema used across tests. Not a shared/domain type —
    local to this test suite only."""

    name: str
    count: int


class TaggedWidget(BaseModel):
    """A `Widget` variant with a non-Optional list field, for exercising
    `parse_structured`'s null-list coercion (structured.py's
    `_coerce_null_lists`) — a small model routinely emits `null` instead of
    `[]` for a list field it has nothing to report for."""

    name: str
    tags: list[str] = []


class FakeProvider:
    """`LLMProvider`-compatible test double.

    `actions` is a queue consumed one per `complete()` call: each item is
    either an `LLMResponse` to return or an `Exception` instance to raise.
    Every request passed in is recorded on `.calls` so tests can assert on
    what `LLMClient` actually sent (model, temperature, timeout, schema).
    """

    def __init__(self, actions: list[LLMResponse | Exception], name: str = "fake") -> None:
        self.name = name
        self._actions = list(actions)
        self.calls: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if not self._actions:
            raise AssertionError("FakeProvider.complete called more times than actions provided")
        action = self._actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    @property
    def call_count(self) -> int:
        return len(self.calls)


def widget_response(name: str = "gizmo", count: int = 3, *, model: str = "llama3") -> LLMResponse:
    """A valid, parseable `LLMResponse` for the `Widget` schema."""
    return LLMResponse(text=f'{{"name": "{name}", "count": {count}}}', model=model)


def malformed_response(model: str = "llama3") -> LLMResponse:
    """An `LLMResponse` with no JSON object at all -> INVALID_RESPONSE."""
    return LLMResponse(text="sorry, I cannot help with that", model=model)


def schema_mismatch_response(model: str = "llama3") -> LLMResponse:
    """Valid JSON, but missing a required field -> SCHEMA_VALIDATION_FAILED."""
    return LLMResponse(text='{"name": "gizmo"}', model=model)


@pytest.fixture
def widget_schema() -> type[Widget]:
    return Widget
