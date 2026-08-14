"""Runtime API clients for Job Matching Service's two documented
dependencies (docs/architecture/dependency-graph.md#2-runtime-api-dependencies):

    Job Matching Service --> Resume/Profile Service (read ResumeProfile list)
    Job Matching Service --> User Service            (GET /users/{id}/preferences,
                                                        read UserPreferences)

These are real out-of-process HTTP calls per the architecture (never a
direct import of another component's module — see
docs/architecture/dependency-graph.md#1-compileimport-dependencies). Job
Discovery Service already solved this exact problem calling the same two
endpoints (`src/jobs/discovery/clients.py`, read-only reference for this
component); this is Job Matching Service's own copy, built directly against
docs/architecture/api-contracts.md#user-service and
docs/architecture/api-contracts.md#resumeprofile-service, since importing
another component's module is forbidden.

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
        (404) rather than raising — `matching.consumers` fills in a
        neutral default `UserPreferences` for this case instead of failing
        the whole match (see that module's docstring).
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
    """`GET /profiles` — api-contracts.md#resumeprofile-service.

    Returns every profile for the user regardless of `ProfileStatus`
    (the API itself does not filter) — `workflows.langgraph.job_matching
    .nodes.load_profiles` is responsible for keeping only `ACTIVE` ones,
    per component-contracts.md#job-matching-service's validation rule.
    """

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
