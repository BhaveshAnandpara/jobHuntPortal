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

from infrastructure.auth import create_access_token
from shared.types.domain.user_preferences import UserPreferences
from shared.types.dto import ResumeProfile
from shared.types.ids import UserId

DEFAULT_BASE_URL = "http://localhost:8000"
_REQUEST_TIMEOUT_SECONDS = 10.0


def _auth_headers(user_id: UserId) -> dict[str, str]:
    """Every route these clients call resolves identity from a bearer
    token (`CurrentUserIdDependency`), not a path/query `user_id` — there
    is no separate service-to-service auth concept in this codebase. A
    backend caller acting on behalf of `user_id` (no end-user session of
    its own) mints a token the same way `users/service.py` does at login,
    via the same shared `infrastructure.auth.create_access_token`."""
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


class UserPreferencesClient:
    """`GET /users/me/preferences` — api-contracts.md#user-service.

    Despite the name, there is no `/users/{user_id}/preferences` route —
    every user-service route derives identity from the bearer token (see
    `_auth_headers`), so the target user is selected by which token is
    minted, not by the URL.
    """

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
        response = await self._get(f"{self._base_url}/users/me/preferences", user_id)
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        response.raise_for_status()
        return UserPreferences.model_validate(response.json())

    async def _get(self, url: str, user_id: UserId) -> httpx.Response:
        headers = _auth_headers(user_id)
        if self._client is not None:
            return await self._client.get(url, headers=headers, timeout=self._timeout)
        async with httpx.AsyncClient() as client:
            return await client.get(url, headers=headers, timeout=self._timeout)


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
        response = await self._get(f"{self._base_url}/profiles", user_id)
        response.raise_for_status()
        return [ResumeProfile.model_validate(item) for item in response.json()]

    async def _get(self, url: str, user_id: UserId) -> httpx.Response:
        headers = _auth_headers(user_id)
        if self._client is not None:
            return await self._client.get(url, headers=headers, timeout=self._timeout)
        async with httpx.AsyncClient() as client:
            return await client.get(url, headers=headers, timeout=self._timeout)


__all__ = ["DEFAULT_BASE_URL", "ProfileServiceClient", "UserPreferencesClient"]
