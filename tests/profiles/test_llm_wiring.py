"""Confirms `profiles/api/dependencies.py`'s LLM wiring resolves the
*calling user's* own `LLMClient` — docs/llm-provider-config/TECH.md Task 5.

Previously this module asserted that `get_llm_client()` returned a cached,
process-wide `LLMClient` built from the operator's `LLMConfig.from_env()`.
That singleton is exactly what per-user provider configuration replaces
(docs/llm-provider-config/PRODUCT.md: every user's AI usage must run on
their own key), so those assertions are gone rather than adapted — the
behavior they described no longer exists. What replaces them is the same
question asked of the new seam: does this dependency hand
`ProfileService` a client belonging to the requesting user, and does it
still leave the four non-parsing routes working for a user who has not
configured a key yet?

No database or live provider is required: `get_user_llm_client` is
substituted at this module's own import site, which is the seam the
dependency actually calls.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from infrastructure.llm import LLMClient, LLMResponse, OllamaProvider
from infrastructure.llm.errors import LlmNotConfiguredError
from profiles.api import dependencies as deps
from profiles.parsing import ExtractedResumeProfile, extract_profile_fields
from shared.types.ids import UserId


def _fake_client() -> LLMClient:
    """A real `LLMClient` over a provider that performs no network I/O on
    construction — stands in for whatever provider a user configured."""
    return LLMClient(provider=OllamaProvider())


@pytest.mark.asyncio
async def test_get_llm_client_resolves_the_calling_users_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = UserId(uuid4())
    expected = _fake_client()
    seen: list[UserId] = []

    async def fake_get_user_llm_client(requested: UserId) -> LLMClient:
        seen.append(requested)
        return expected

    monkeypatch.setattr(deps, "get_user_llm_client", fake_get_user_llm_client)

    client = await deps.get_llm_client(user_id)

    assert client is expected
    assert seen == [user_id]


@pytest.mark.asyncio
async def test_get_llm_client_is_not_cached_across_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The replaced implementation cached one client for the whole process.
    Two different users must now get two different clients — a cache here
    would be the cross-user key sharing this feature exists to remove.
    """
    clients = {UserId(uuid4()): _fake_client(), UserId(uuid4()): _fake_client()}

    async def fake_get_user_llm_client(requested: UserId) -> LLMClient:
        return clients[requested]

    monkeypatch.setattr(deps, "get_user_llm_client", fake_get_user_llm_client)

    first_id, second_id = list(clients)
    assert await deps.get_llm_client(first_id) is clients[first_id]
    assert await deps.get_llm_client(second_id) is clients[second_id]


@pytest.mark.asyncio
async def test_get_llm_client_returns_none_when_the_user_has_no_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`None` — not an exception — because this dependency is resolved for
    every route on the router, including the four that never touch the LLM.
    `ProfileService` already treats `None` as "no usable client" and fails
    only the parsing workflow with `LLM_PROVIDER_ERROR`.
    """
    user_id = UserId(uuid4())

    async def fake_get_user_llm_client(requested: UserId) -> LLMClient:
        raise LlmNotConfiguredError(requested)

    monkeypatch.setattr(deps, "get_user_llm_client", fake_get_user_llm_client)

    assert await deps.get_llm_client(user_id) is None


@pytest.mark.asyncio
async def test_get_llm_client_returns_none_for_an_unauthenticated_request() -> None:
    """`DELETE /resumes/{id}` and `GET /profiles/{id}` carry no token today
    (the latter is called server-to-server by `outreach.clients`), so the
    dependency must resolve to `None` rather than 401 — this task must not
    add authentication to a route that had none.
    """
    assert await deps.get_llm_client(None) is None


@pytest.mark.asyncio
async def test_resolved_client_resolves_structured_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end through the real `LLMClient` wiring the dependency hands
    to `ProfileService`, with the one network-performing call
    (`OllamaProvider.complete`) monkeypatched so no live server is required.
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

    async def fake_get_user_llm_client(requested: UserId) -> LLMClient:
        return _fake_client()

    monkeypatch.setattr(deps, "get_user_llm_client", fake_get_user_llm_client)

    client = await deps.get_llm_client(UserId(uuid4()))
    assert client is not None
    result = extract_profile_fields("irrelevant raw resume text", client=client)

    assert isinstance(result, ExtractedResumeProfile)
    assert result.title == "Registered Nurse"
    assert result.certifications == ["RN"]
    assert result.industries == ["Healthcare"]
