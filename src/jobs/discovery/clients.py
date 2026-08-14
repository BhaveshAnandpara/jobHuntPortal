"""Runtime API clients for Job Discovery Service's two documented
dependencies (docs/architecture/dependency-graph.md#2-runtime-api-dependencies):

    Job Discovery Service --> User Service          (read UserPreferences)
    Job Discovery Service --> Resume/Profile Service (read ResumeProfile list)

These are real out-of-process HTTP calls per the architecture (never a
direct import of another component's module — see
docs/architecture/dependency-graph.md#1-compileimport-dependencies). No
shared client library for these endpoints is published anywhere in
`infrastructure/`, so this is a thin, local `httpx` client built directly
against the documented contracts in
docs/architecture/api-contracts.md#user-service and
docs/architecture/api-contracts.md#resumeprofile-service.

Base URLs default to the single-process modular-monolith deployment
(docs/architecture/overview.md#deployment-model), where every component's
router is mounted in the same `api/main.py` app, and are overridable via
`USER_SERVICE_URL` / `PROFILE_SERVICE_URL` for a future split-service
deployment.
"""

from __future__ import annotations

import os

import httpx

from shared.types.domain.user_preferences import UserPreferences
from shared.types.dto import ResumeProfile
from shared.types.ids import UserId

DEFAULT_BASE_URL = "http://localhost:8000"
_REQUEST_TIMEOUT_SECONDS = 10.0


class UserPreferencesClient:
    """`GET /users/{user_id}/preferences` — api-contracts.md#user-service."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("USER_SERVICE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self._client = client
        self._timeout = timeout

    async def get_preferences(self, user_id: UserId) -> UserPreferences | None:
        """Returns `None` if the user has no preferences configured yet
        (404) rather than raising — a source can still run using
        `JobSource.query_config` and profile data alone.
        """
        response = await self._get(f"{self._base_url}/users/{user_id}/preferences")
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        response.raise_for_status()
        return UserPreferences.model_validate(response.json())

    async def _get(self, url: str) -> httpx.Response:
        if self._client is not None:
            return await self._client.get(url, timeout=self._timeout)
        async with httpx.AsyncClient() as client:
            return await client.get(url, timeout=self._timeout)


class ProfileServiceClient:
    """`GET /profiles` — api-contracts.md#resumeprofile-service."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("PROFILE_SERVICE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self._client = client
        self._timeout = timeout

    async def list_profiles(self, user_id: UserId) -> list[ResumeProfile]:
        response = await self._get(
            f"{self._base_url}/profiles", params={"user_id": str(user_id)}
        )
        response.raise_for_status()
        return [ResumeProfile.model_validate(item) for item in response.json()]

    async def _get(self, url: str, *, params: dict[str, str]) -> httpx.Response:
        if self._client is not None:
            return await self._client.get(url, params=params, timeout=self._timeout)
        async with httpx.AsyncClient() as client:
            return await client.get(url, params=params, timeout=self._timeout)


__all__ = ["DEFAULT_BASE_URL", "ProfileServiceClient", "UserPreferencesClient"]
