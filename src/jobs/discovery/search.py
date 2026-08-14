"""Automatic discovery run — searches configured JobSources using a user's
preferences and profiles as search context, and normalizes results.
See docs/architecture/component-contracts.md#automatic-discovery-run-scheduledinternal-trigger.

This module is the pure search+normalize step: build a coarse
`JobSearchQuery` from `JobSource.query_config` + preferences + profiles,
run it through the External Integrations Layer's job board search client,
and turn each raw `JobSearchHit` into a `NormalizedJob` by reusing
`jobs.ingestion.extraction.extract_job_from_url` on the hit's URL (the same
fetch+LLM-extract pipeline the manual path uses — this is why
`JOB_FETCH_FAILED`/`LLM_PROVIDER_ERROR` are documented as per-posting
errors for the automatic run: a `JobSearchHit` only carries a sparse
`snippet`, not the full posting text `NormalizedJob.description` requires).

Persistence (INSERT `jobs`), dedup, publishing `JobDiscoveredEvent`, and
updating `job_sources.last_run_at` are NOT done here — that orchestration
lives in `jobs.discovery.service.run_discovery`, mirroring the
extraction.py/service.py split on the ingestion side.

Contract note (flagged, not silently resolved): component-contracts.md
says profiles are read "for relevance pre-filtering before publishing
candidates", but service-boundaries.md's Job Discovery Service entry says
it must "never filter by relevance beyond very coarse source-level query
parameters" and about_project.md (User Journey 3) says discovery "should
not make the final decision about whether a job is worth applying to.
That responsibility belongs to the matching stage." This implementation
follows the more specific/repeated rule: profiles and preferences are used
only to build coarse search keywords (`_build_query` below); no discovered
posting is ever dropped here because of low apparent relevance to a
profile.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from infrastructure.external.job_search import (
    JobBoardSearchClient,
    JobSearchHit,
    JobSearchQuery,
)
from jobs.ingestion.errors import JobIngestionError
from jobs.ingestion.extraction import (
    PageFetcher,
    StructuredExtractor,
    extract_job_from_url,
)
from jobs.ingestion.service import canonicalize_url
from shared.types.domain.job_source import JobSource
from shared.types.domain.user_preferences import UserPreferences
from shared.types.dto import NormalizedJob, ResumeProfile
from shared.types.ids import JobId, UserId

_DEFAULT_MAX_RESULTS = 25


def _build_query(
    source: JobSource,
    preferences: UserPreferences | None,
    profiles: list[ResumeProfile],
) -> JobSearchQuery:
    """Coarse, source-level search parameters only — see the module
    docstring's flagged note on why this never filters by relevance.
    """
    query_config = source.query_config or {}

    keywords: list[str] = list(query_config.get("keywords", []))
    if preferences is not None:
        keywords.extend(preferences.target_roles)
    for profile in profiles:
        keywords.extend(profile.target_roles)
        keywords.extend(profile.skills)

    seen: set[str] = set()
    deduped_keywords: list[str] = []
    for keyword in keywords:
        cleaned = str(keyword).strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            deduped_keywords.append(cleaned)

    location = query_config.get("location")
    if not location and preferences is not None and preferences.target_locations:
        location = preferences.target_locations[0]

    remote_preference = preferences.remote_preference if preferences is not None else None

    reserved = {"keywords", "location", "max_results"}
    provider_params = {
        str(key): str(value)
        for key, value in query_config.items()
        if key not in reserved
    }

    return JobSearchQuery(
        keywords=deduped_keywords,
        location=location,
        remote_preference=remote_preference,
        max_results=int(query_config.get("max_results", _DEFAULT_MAX_RESULTS)),
        provider_params=provider_params,
    )


async def _normalize_hit(
    hit: JobSearchHit,
    *,
    user_id: UserId,
    page_fetcher: PageFetcher,
    extractor: StructuredExtractor,
) -> NormalizedJob | None:
    """Fetch+extract the full posting behind `hit.source_url` and build a
    `NormalizedJob`. Returns `None` (never raises) if extraction fails for
    this one posting — callers must not let one bad posting abort the run.
    """
    try:
        fields = await extract_job_from_url(
            hit.source_url, page_fetcher=page_fetcher, extractor=extractor
        )
    except JobIngestionError:
        return None

    return NormalizedJob(
        job_id=JobId(uuid4()),
        user_id=user_id,
        company=fields.company,
        title=fields.title,
        location=fields.location or hit.location,
        description=fields.description,
        extracted_skills=fields.extracted_skills,
        experience_required=fields.experience_required,
        source_type=hit.source_type,
        source_url=canonicalize_url(hit.source_url),
        discovered_at=datetime.now(UTC),
    )


async def discover_jobs_for_source(
    source: JobSource,
    *,
    preferences: UserPreferences | None,
    profiles: list[ResumeProfile],
    search_client: JobBoardSearchClient,
    page_fetcher: PageFetcher,
    extractor: StructuredExtractor,
) -> list[NormalizedJob]:
    """Search `source` and return one `NormalizedJob` per successfully
    extracted posting. A single posting's extraction failure is skipped,
    never raised (component-contracts.md: "a single failure doesn't stop
    the run"). A search-provider-level failure (the board itself is down)
    propagates to the caller, since there is nothing to normalize.
    """
    if not source.enabled:
        return []

    query = _build_query(source, preferences, profiles)
    hits = await search_client.search(query)

    results: list[NormalizedJob] = []
    for hit in hits:
        normalized = await _normalize_hit(
            hit,
            user_id=source.user_id,
            page_fetcher=page_fetcher,
            extractor=extractor,
        )
        if normalized is None:
            continue
        results.append(normalized)
    return results


__all__ = ["discover_jobs_for_source"]
