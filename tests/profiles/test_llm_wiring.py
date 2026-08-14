"""Confirms profiles/api/dependencies.py:get_llm_client() resolves a real
`infrastructure.llm.LLMClient` by default (OllamaProvider built from
`LLMConfig.from_env()`) — closing out the placeholder documented in the
Wave 1 report, now that the LLM Provider Layer exports a concrete
implementation.

No live Ollama server required: constructing `OllamaProvider` performs no
network I/O (it only opens a connection lazily inside `.complete()`), and
the one test that exercises an actual `LLMClient.complete_structured()`
call monkeypatches `OllamaProvider.complete` at the transport boundary
rather than reaching a real server.
"""

from __future__ import annotations

import json

import pytest

from infrastructure.llm import LLMClient, LLMResponse, OllamaProvider
from profiles.api import dependencies as deps
from profiles.parsing import ExtractedResumeProfile, extract_profile_fields


@pytest.fixture(autouse=True)
def _reset_llm_client_singleton():
    """`get_llm_client()` caches a process-wide singleton on the module —
    reset it before and after each test so tests don't leak state into each
    other (or into other test modules importing the same dependencies
    module).
    """
    deps._llm_client = None
    yield
    deps._llm_client = None


def test_get_llm_client_defaults_to_ollama_provider() -> None:
    client = deps.get_llm_client()

    assert isinstance(client, LLMClient)
    assert isinstance(client.provider, OllamaProvider)


def test_get_llm_client_is_a_cached_singleton() -> None:
    first = deps.get_llm_client()
    second = deps.get_llm_client()

    assert first is second


def test_default_llm_client_resolves_structured_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end through the real `LLMClient`/`OllamaProvider` wiring
    `get_llm_client()` builds, with the one network-performing call
    (`OllamaProvider.complete`) monkeypatched so no live Ollama server is
    required.
    """
    fields = {
        "title": "Registered Nurse",
        "summary": None,
        "skills": ["Patient Care", "Triage"],
        "experience_years": 7.0,
        "seniority": "Senior",
        "education": [],
        "certifications": ["RN"],
        "projects": [],
        "industries": ["Healthcare"],
        "target_roles": ["Charge Nurse"],
    }

    def fake_complete(self: OllamaProvider, request) -> LLMResponse:
        return LLMResponse(text=json.dumps(fields), model=request.model)

    monkeypatch.setattr(OllamaProvider, "complete", fake_complete)

    client = deps.get_llm_client()
    result = extract_profile_fields("irrelevant raw resume text", client=client)

    assert isinstance(result, ExtractedResumeProfile)
    assert result.title == "Registered Nurse"
    assert result.certifications == ["RN"]
    assert result.industries == ["Healthcare"]
