"""Runtime API clients for Outreach Service's three documented dependencies
(docs/architecture/dependency-graph.md#2-runtime-api-dependencies):

    Outreach Service --> Job Matching Service    (GET /jobs/{job_id}/matches,
                                                    read selected_profile_id/
                                                    selected_resume_id)
    Outreach Service --> Resume/Profile Service   (GET /profiles/{profile_id},
                                                    read the selected resume/
                                                    profile for tone/content,
                                                    by the profile_id obtained
                                                    from the call above)
    Outreach Service --> Job Ingestion Service    (GET /jobs/{job_id}, read
                                                    company/title for message
                                                    personalization)

All three are real out-of-process HTTP calls per the architecture (never a
direct import of another component's module — see
docs/architecture/dependency-graph.md#1-compileimport-dependencies).
`ContactRankingResult` (the `contacts.found` payload) carries no
`selected_resume_id`/`selected_profile_id`/`company`/`title` —
component-contracts.md's Outreach Service `contacts.found` consumer entry
has always required the first two-hop chain to obtain the former; the
`JobIngestionClient` closes a related, later-discovered gap for the
latter (neither `ContactRankingResult` nor `JobMatchResponse` carries the
job's own `company`/`title` — only `GET /jobs/{job_id}`, the read path
Job Ingestion Service already exposes for exactly this "full job detail"
need, does — see dependency-graph.md's note on this edge).
`matching/clients.py` already solved this exact "own copy of a
same-shaped HTTP client" problem for a sibling component; this is
Outreach Service's own copy, built directly against
docs/architecture/api-contracts.md#job-matching-service,
docs/architecture/api-contracts.md#resumeprofile-service, and
docs/architecture/api-contracts.md#job-ingestion-service, since importing
another component's module is forbidden.

Base URLs default to the single-process modular-monolith deployment
(docs/architecture/overview.md#deployment-model), where every component's
router is mounted in the same `api/main.py` app, and are overridable via
`MATCHING_SERVICE_URL` / `PROFILE_SERVICE_URL` / `JOB_INGESTION_SERVICE_URL`
for a future split-service deployment.
"""

from __future__ import annotations

import os

import httpx

from shared.types.api.jobs import JobResponse
from shared.types.api.matching import JobMatchResponse
from shared.types.dto import ResumeProfile
from shared.types.ids import JobId, ProfileId

DEFAULT_BASE_URL = "http://localhost:8000"
_REQUEST_TIMEOUT_SECONDS = 10.0


class JobMatchClient:
    """`GET /jobs/{job_id}/matches` — api-contracts.md#job-matching-service."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("MATCHING_SERVICE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self._client = client
        self._timeout = timeout

    async def get_match(self, job_id: JobId) -> JobMatchResponse | None:
        """Returns `None` on 404 (no `JobMatch` yet for this job) rather
        than raising — `outreach.consumers`' `contacts.found` handler
        normalizes a missing match to `OUTREACH_GENERATION_FAILED` and
        raises `OutreachError`, per this component's task brief ("normalize
        to OUTREACH_GENERATION_FAILED ... do not silently skip or
        half-generate a draft").
        """
        response = await self._get(f"{self._base_url}/jobs/{job_id}/matches")
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        response.raise_for_status()
        return JobMatchResponse.model_validate(response.json())

    async def _get(self, url: str) -> httpx.Response:
        if self._client is not None:
            return await self._client.get(url, timeout=self._timeout)
        async with httpx.AsyncClient() as client:
            return await client.get(url, timeout=self._timeout)


class ProfileServiceClient:
    """`GET /profiles/{profile_id}` — api-contracts.md#resumeprofile-service."""

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

    async def get_profile(self, profile_id: ProfileId) -> ResumeProfile | None:
        """Returns `None` on 404 (profile archived/unknown) rather than
        raising — same normalize-then-raise-OutreachError handling as
        `JobMatchClient.get_match` above.
        """
        response = await self._get(f"{self._base_url}/profiles/{profile_id}")
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        response.raise_for_status()
        return ResumeProfile.model_validate(response.json())

    async def _get(self, url: str) -> httpx.Response:
        if self._client is not None:
            return await self._client.get(url, timeout=self._timeout)
        async with httpx.AsyncClient() as client:
            return await client.get(url, timeout=self._timeout)


class JobIngestionClient:
    """`GET /jobs/{job_id}` — api-contracts.md#job-ingestion-service.

    Read-only access to a job's canonical `company`/`title` (and other
    posting detail), for message-personalization context that no event
    payload this service consumes carries.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("JOB_INGESTION_SERVICE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self._client = client
        self._timeout = timeout

    async def get_job(self, job_id: JobId) -> JobResponse | None:
        """Returns `None` on 404 rather than raising — same
        normalize-then-raise-OutreachError handling as
        `JobMatchClient.get_match`/`ProfileServiceClient.get_profile`.
        """
        response = await self._get(f"{self._base_url}/jobs/{job_id}")
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        response.raise_for_status()
        return JobResponse.model_validate(response.json())

    async def _get(self, url: str) -> httpx.Response:
        if self._client is not None:
            return await self._client.get(url, timeout=self._timeout)
        async with httpx.AsyncClient() as client:
            return await client.get(url, timeout=self._timeout)


__all__ = [
    "DEFAULT_BASE_URL",
    "JobIngestionClient",
    "JobMatchClient",
    "ProfileServiceClient",
]
