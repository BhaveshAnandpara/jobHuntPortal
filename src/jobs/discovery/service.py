"""Automatic Discovery Run orchestration.
See docs/architecture/component-contracts.md#automatic-discovery-run-scheduledinternal-trigger.

Trigger: scheduled run (or a future manual admin trigger) — no public API
is required by the current product scope, so this is a plain importable
function a scheduler (or a test, or an ad hoc admin script) calls directly;
it is deliberately not wired to any FastAPI route.

For every enabled `JobSource`: read preferences/profiles via the two
runtime API dependencies, search+normalize via `search.py`, dedupe against
the existing `jobs` rows for that user (same natural key the manual path
uses — canonical `source_url`), insert, and publish `JobDiscoveredEvent`.
A single posting's failure — or even a whole source's search failure —
must not abort the run for the other sources (component-contracts.md).
"""

from __future__ import annotations

from datetime import UTC, datetime

from infrastructure.external.job_search import JobBoardSearchClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.logging import get_logger
from jobs.discovery.clients import ProfileServiceClient, UserPreferencesClient
from jobs.discovery.events import publish_job_discovered
from jobs.discovery.search import discover_jobs_for_source
from jobs.ingestion.extraction import PageFetcher, StructuredExtractor
from jobs.repository import JobRepository, JobSourceRepository
from shared.types.domain.job import Job
from shared.types.dto import NormalizedJob
from shared.types.enums import JobProcessingStatus
from shared.types.ids import JobSourceId

logger = get_logger(__name__)


def _to_job(normalized: NormalizedJob, *, source_id: JobSourceId) -> Job:
    return Job(
        id=normalized.job_id,
        user_id=normalized.user_id,
        source_type=normalized.source_type,
        source_id=source_id,
        source_url=normalized.source_url,
        company=normalized.company,
        title=normalized.title,
        location=normalized.location,
        description_raw=normalized.description,
        extracted_skills=normalized.extracted_skills,
        experience_required=normalized.experience_required,
        processing_status=JobProcessingStatus.NORMALIZED,
        discovered_at=normalized.discovered_at,
    )


async def _run_one_source(
    source,
    *,
    job_repository: JobRepository,
    user_client: UserPreferencesClient,
    profile_client: ProfileServiceClient,
    search_client: JobBoardSearchClient,
    page_fetcher: PageFetcher,
    extractor: StructuredExtractor,
    producer: EventProducer,
) -> list[Job]:
    try:
        preferences = await user_client.get_preferences(source.user_id)
    except Exception as exc:  # noqa: BLE001 - intentional: a User Service
        # outage must degrade this one source (search still runs on
        # query_config + profiles alone), never abort the whole run.
        logger.warning(
            "job source %s: failed to read UserPreferences for user %s: %s",
            source.id,
            source.user_id,
            exc,
        )
        preferences = None

    try:
        profiles = await profile_client.list_profiles(source.user_id)
    except Exception as exc:  # noqa: BLE001 - intentional, same reasoning as above
        logger.warning(
            "job source %s: failed to read profiles for user %s: %s",
            source.id,
            source.user_id,
            exc,
        )
        profiles = []

    try:
        normalized_jobs = await discover_jobs_for_source(
            source,
            preferences=preferences,
            profiles=profiles,
            search_client=search_client,
            page_fetcher=page_fetcher,
            extractor=extractor,
        )
    except Exception as exc:  # noqa: BLE001 - intentional: the search
        # provider itself failed for this source — skip it, try the next
        # source. component-contracts.md: "a single failure doesn't stop
        # the run."
        logger.warning("job source %s: search failed, skipping: %s", source.id, exc)
        return []

    persisted: list[Job] = []
    for normalized in normalized_jobs:
        try:
            existing = await job_repository.find_by_source_url(
                normalized.user_id, normalized.source_url
            )
            if existing is not None:
                continue  # dedup — same natural key as the manual path

            job = _to_job(normalized, source_id=source.id)
            await job_repository.insert_discovered(job)
            publish_job_discovered(normalized, producer=producer)
            persisted.append(job)
        except Exception as exc:  # noqa: BLE001 - intentional: a single
            # posting's failure must not abort the source's remaining
            # postings or the overall run (component-contracts.md).
            logger.warning(
                "job source %s: failed to persist/publish posting %s: %s",
                source.id,
                normalized.source_url,
                exc,
            )
            continue

    return persisted


async def run_discovery(
    *,
    source_repository: JobSourceRepository,
    job_repository: JobRepository,
    user_client: UserPreferencesClient,
    profile_client: ProfileServiceClient,
    search_client: JobBoardSearchClient,
    page_fetcher: PageFetcher,
    extractor: StructuredExtractor,
    producer: EventProducer,
) -> list[Job]:
    """Run automatic discovery for every enabled `JobSource`. Returns every
    newly persisted `Job` across all sources (excludes dedup skips).
    """
    persisted: list[Job] = []
    for source in await source_repository.list_enabled():
        persisted.extend(
            await _run_one_source(
                source,
                job_repository=job_repository,
                user_client=user_client,
                profile_client=profile_client,
                search_client=search_client,
                page_fetcher=page_fetcher,
                extractor=extractor,
                producer=producer,
            )
        )
        await source_repository.mark_run(source.id, datetime.now(UTC))

    return persisted


__all__ = ["run_discovery"]
