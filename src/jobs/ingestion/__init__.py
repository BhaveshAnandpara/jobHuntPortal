"""Job Ingestion Service — the manual entry point: user pastes a URL,
service extracts a normalized job posting.
See docs/architecture/service-boundaries.md#job-ingestion-service.

APIs: `/jobs/ingest-url`. Kafka produced: `jobs.discovered`
(source_type=MANUAL_URL). Kafka consumed: none.
Dependencies: Playwright/BeautifulSoup (page fetch), LLM Provider Layer
(extraction).

Explicitly outside this component's responsibility: deciding relevance or
matching (Job Matching Service); automatic search (Job Discovery Service).
"""
