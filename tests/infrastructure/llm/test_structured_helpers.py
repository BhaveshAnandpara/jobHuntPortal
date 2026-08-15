"""Tests for the pure structured-output helpers in structured.py:
schema_instructions, extract_json_object, parse_structured, repair_prompt.
"""

from __future__ import annotations

import pytest

from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from infrastructure.llm.structured import (
    extract_json_object,
    parse_structured,
    repair_prompt,
    schema_instructions,
)
from tests.infrastructure.llm.conftest import TaggedWidget, Widget


def test_schema_instructions_embeds_json_schema():
    text = schema_instructions(Widget)
    assert "JSON object" in text
    assert '"name"' in text
    assert '"count"' in text
    assert "markdown fences" in text


def test_extract_json_object_plain():
    assert extract_json_object('{"a": 1}') == '{"a": 1}'


def test_extract_json_object_strips_surrounding_prose():
    text = 'Sure, here you go:\n```json\n{"a": 1}\n```\nHope that helps.'
    assert extract_json_object(text) == '{"a": 1}'


def test_extract_json_object_raises_when_no_braces():
    with pytest.raises(ValueError):
        extract_json_object("no json here")


def test_extract_json_object_raises_when_braces_reversed():
    with pytest.raises(ValueError):
        extract_json_object("} this is backwards {")


def test_parse_structured_success():
    result = parse_structured(
        '{"name": "gizmo", "count": 3}',
        Widget,
        provider="fake",
        model="llama3",
    )
    assert result == Widget(name="gizmo", count=3)


def test_parse_structured_invalid_json_raises_normalized_error():
    with pytest.raises(LLMProviderError) as excinfo:
        parse_structured("not json at all", Widget, provider="fake", model="llama3")
    err = excinfo.value
    assert err.reason == LLMFailureReason.INVALID_RESPONSE
    assert err.provider == "fake"
    assert err.model == "llama3"
    assert err.raw_text == "not json at all"


def test_parse_structured_schema_mismatch_raises_normalized_error():
    with pytest.raises(LLMProviderError) as excinfo:
        parse_structured('{"name": "gizmo"}', Widget, provider="fake", model="llama3")
    err = excinfo.value
    assert err.reason == LLMFailureReason.SCHEMA_VALIDATION_FAILED


def test_parse_structured_records_attempts():
    with pytest.raises(LLMProviderError) as excinfo:
        parse_structured("nope", Widget, provider="fake", model="llama3", attempts=4)
    assert excinfo.value.attempts == 4


def test_parse_structured_coerces_explicit_null_list_to_empty_list():
    """A small model emitting `"tags": null` instead of `"tags": []` must
    not fail validation — see structured.py's `_coerce_null_lists`."""
    result = parse_structured(
        '{"name": "gizmo", "tags": null}',
        TaggedWidget,
        provider="fake",
        model="llama3.2:1b",
    )
    assert result == TaggedWidget(name="gizmo", tags=[])


def test_parse_structured_leaves_absent_list_field_to_its_own_default():
    result = parse_structured(
        '{"name": "gizmo"}',
        TaggedWidget,
        provider="fake",
        model="llama3.2:1b",
    )
    assert result.tags == []


def test_parse_structured_leaves_populated_list_field_untouched():
    result = parse_structured(
        '{"name": "gizmo", "tags": ["a", "b"]}',
        TaggedWidget,
        provider="fake",
        model="llama3.2:1b",
    )
    assert result.tags == ["a", "b"]


def test_repair_prompt_includes_original_prompt_and_problem():
    prompt = repair_prompt("original prompt text", "bad response", "missing field 'count'")
    assert "original prompt text" in prompt
    assert "bad response" in prompt
    assert "missing field 'count'" in prompt
    assert "corrected JSON object" in prompt
