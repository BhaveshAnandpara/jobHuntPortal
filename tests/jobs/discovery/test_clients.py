"""Tests for `jobs.discovery.clients` — the runtime API clients for Job
Discovery Service's two documented dependencies (User Service preferences,
Resume/Profile Service profiles). Uses `httpx.MockTransport`, no live
services required.
"""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from jobs.discovery.clients import ProfileServiceClient, UserPreferencesClient
from shared.types.enums import ProfileStatus, RemoteWorkPreference
from shared.types.ids import UserId


def _preferences_payload(user_id: str) -> dict:
    return {
        "id": str(uuid4()),
        "user_id": user_id,
        "target_roles": ["Mechanical Engineer"],
        "target_locations": ["Berlin"],
        "remote_preference": RemoteWorkPreference.HYBRID.value,
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
        "title": "Mechanical Engineer",
        "skills": ["CAD"],
        "target_roles": ["Design Engineer"],
        "status": ProfileStatus.ACTIVE.value,
    }


@pytest.mark.asyncio
async def test_get_preferences_returns_parsed_model() -> None:
    user_id = UserId(uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/users/{user_id}/preferences"
        return httpx.Response(200, json=_preferences_payload(str(user_id)))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = UserPreferencesClient(base_url="http://user-service.local", client=http)
        preferences = await client.get_preferences(user_id)

    assert preferences is not None
    assert preferences.target_roles == ["Mechanical Engineer"]
    assert preferences.remote_preference is RemoteWorkPreference.HYBRID


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
        assert request.url.params["user_id"] == str(user_id)
        return httpx.Response(200, json=[_profile_payload(str(user_id))])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ProfileServiceClient(base_url="http://profile-service.local", client=http)
        profiles = await client.list_profiles(user_id)

    assert len(profiles) == 1
    assert profiles[0].skills == ["CAD"]
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
