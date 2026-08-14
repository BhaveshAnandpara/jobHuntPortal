"""Job Discovery Service — the automatic entry point: proactively finds
postings using a user's profiles and preferences as search context.
See docs/architecture/service-boundaries.md#job-discovery-service.

APIs: `/job-sources`. Kafka produced: `jobs.discovered` (source_type !=
MANUAL_URL). Kafka consumed: none.
Dependencies: job board search APIs/scrapers, LLM Provider Layer, User
Service (read UserPreferences via API), Resume/Profile Service (read
ResumeProfile list via API).

Explicitly outside this component's responsibility: deciding whether a
discovered posting is worth pursuing — "It should not make the final
decision about whether a job is worth applying to. That responsibility
belongs to the matching stage." (about_project.md)
"""
