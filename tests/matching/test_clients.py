"""Tests for `matching.clients` — the runtime API clients for Job Matching
Service's two documented dependencies (User Service preferences,
Resume/Profile Service profiles). Uses `httpx.MockTransport`, no live
services required.

Mirrors `tests/jobs/discovery/test_clients.py` (jobs.discovery.clients has
the identical shape); this component can't import that module directly
(no cross-component imports), so the client class — and its tests — are a
separate copy.
"""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from infrastructure.auth import decode_access_token
from matching.clients import ProfileServiceClient, UserPreferencesClient
from shared.types.enums import ProfileStatus, RemoteWorkPreference
from shared.types.ids import UserId


def _bearer_user_id(request: httpx.Request) -> UserId:
    """Decode the `Authorization: Bearer <token>` header these clients
    attach, proving it's a real, verifiable token for `user_id` — every
    route they call resolves identity from this token, not a path/query
    `user_id` (there is no service-to-service auth concept in this
    codebase; a backend caller with no end-user session mints one the same
    way `users/service.py` does at login)."""
    scheme, _, token = request.headers["authorization"].partition(" ")
    assert scheme == "Bearer"
    return decode_access_token(token)


def _preferences_payload(user_id: str) -> dict:
    return {
        "id": str(uuid4()),
        "user_id": user_id,
        "target_roles": ["Backend Engineer"],
        "target_locations": ["Remote"],
        "remote_preference": RemoteWorkPreference.REMOTE.value,
        "excluded_companies": [],
        "min_salary": None,
        "salary_currency": None,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def _profile_payload(user_id: str) -> dict:
    return {
        "profile_id": str(uuid4()),
        "resume_id": str(uuid4()),
        "user_id": user_id,
        "title": "Backend Engineer",
        "skills": ["Python"],
        "target_roles": ["Senior Backend Engineer"],
        "status": ProfileStatus.ACTIVE.value,
    }


@pytest.mark.asyncio
async def test_get_preferences_returns_parsed_model() -> None:
    user_id = UserId(uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users/me/preferences"
        assert _bearer_user_id(request) == user_id
        return httpx.Response(200, json=_preferences_payload(str(user_id)))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = UserPreferencesClient(base_url="http://user-service.local", client=http)
        preferences = await client.get_preferences(user_id)

    assert preferences is not None
    assert preferences.target_roles == ["Backend Engineer"]
    assert preferences.remote_preference is RemoteWorkPreference.REMOTE


@pytest.mark.asyncio
async def test_get_preferences_returns_none_on_404() -> None:
    user_id = UserId(uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": {"code": "NOT_FOUND"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = UserPreferencesClient(base_url="http://user-service.local", client=http)
        preferences = await client.get_preferences(user_id)

    assert preferences is None


@pytest.mark.asyncio
async def test_get_preferences_raises_on_server_error() -> None:
    user_id = UserId(uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = UserPreferencesClient(base_url="http://user-service.local", client=http)
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_preferences(user_id)


@pytest.mark.asyncio
async def test_list_profiles_returns_parsed_models() -> None:
    user_id = UserId(uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/profiles"
        assert _bearer_user_id(request) == user_id
        return httpx.Response(200, json=[_profile_payload(str(user_id))])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ProfileServiceClient(base_url="http://profile-service.local", client=http)
        profiles = await client.list_profiles(user_id)

    assert len(profiles) == 1
    assert profiles[0].skills == ["Python"]
    assert profiles[0].status is ProfileStatus.ACTIVE


@pytest.mark.asyncio
async def test_list_profiles_empty_list() -> None:
    user_id = UserId(uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ProfileServiceClient(base_url="http://profile-service.local", client=http)
        profiles = await client.list_profiles(user_id)

    assert profiles == []
