"""Tests for the pure structured-output helpers in structured.py:
schema_instructions, extract_json_object, parse_structured, repair_prompt.
"""

from __future__ import annotations

import json

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


def test_parse_structured_unwraps_schema_name_wrapper():
    """A small model sometimes wraps its answer in the schema's own class
    name instead of returning the bare object — see structured.py's
    `_unwrap_schema_wrapper`."""
    result = parse_structured(
        '{"Widget": {"name": "gizmo", "count": 3}}',
        Widget,
        provider="fake",
        model="llama3.2:1b",
    )
    assert result == Widget(name="gizmo", count=3)


@pytest.mark.parametrize("wrapper_key", ["profile", "result", "data", "response"])
def test_parse_structured_unwraps_common_wrapper_keys(wrapper_key: str):
    result = parse_structured(
        f'{{"{wrapper_key}": {{"name": "gizmo", "count": 3}}}}',
        Widget,
        provider="fake",
        model="llama3.2:1b",
    )
    assert result == Widget(name="gizmo", count=3)


def test_parse_structured_does_not_unwrap_when_outer_key_is_a_real_field():
    """A single-key object whose key is itself a valid schema field (e.g. a
    schema with exactly one field) must be validated as-is, not treated as
    a wrapper."""
    with pytest.raises(LLMProviderError) as excinfo:
        parse_structured(
            '{"name": {"count": 3}}', Widget, provider="fake", model="llama3"
        )
    assert excinfo.value.reason == LLMFailureReason.SCHEMA_VALIDATION_FAILED


def test_parse_structured_does_not_unwrap_when_inner_dict_shares_no_fields():
    """A single-key object whose nested dict shares no field names with the
    schema is not a recognizable wrapper — surface the original validation
    error instead of guessing."""
    with pytest.raises(LLMProviderError) as excinfo:
        parse_structured(
            '{"note": {"unrelated": "value"}}', Widget, provider="fake", model="llama3"
        )
    assert excinfo.value.reason == LLMFailureReason.SCHEMA_VALIDATION_FAILED


def test_parse_structured_unwraps_then_coerces_null_lists():
    """The wrapper-unwrap and null-list coercion fixes must compose: a
    wrapped response with an explicit `null` list field still resolves."""
    result = parse_structured(
        '{"TaggedWidget": {"name": "gizmo", "tags": null}}',
        TaggedWidget,
        provider="fake",
        model="llama3.2:1b",
    )
    assert result == TaggedWidget(name="gizmo", tags=[])


def test_parse_structured_rejects_echoed_json_schema_as_invalid_response():
    """A small model sometimes echoes the JSON Schema it was given back as
    its "answer" instead of producing data — see structured.py's
    `_looks_like_schema_echo`. Because `model_json_schema()` always
    includes a top-level `title` set to the class name, this would
    otherwise silently validate as garbage data (title="Widget") rather
    than fail loudly."""
    schema_text = json.dumps(Widget.model_json_schema())
    with pytest.raises(LLMProviderError) as excinfo:
        parse_structured(schema_text, Widget, provider="fake", model="llama3.2:1b")
    assert excinfo.value.reason == LLMFailureReason.INVALID_RESPONSE


def test_parse_structured_does_not_flag_real_data_as_a_schema_echo():
    """A legitimate response never has `type`/`properties` as top-level
    schema field names in this codebase, but confirm the happy path is
    unaffected regardless."""
    result = parse_structured(
        '{"name": "gizmo", "count": 3}', Widget, provider="fake", model="llama3"
    )
    assert result == Widget(name="gizmo", count=3)


def test_repair_prompt_includes_original_prompt_and_problem():
    prompt = repair_prompt("original prompt text", "bad response", "missing field 'count'")
    assert "original prompt text" in prompt
    assert "bad response" in prompt
    assert "missing field 'count'" in prompt
    assert "corrected JSON object" in prompt
