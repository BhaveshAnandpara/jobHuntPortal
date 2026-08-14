"""Job Matching Service — compares a discovered job against every one of a
user's active profiles, selects the best-fit resume, scores the match, and
gates whether the opportunity proceeds ("the matching stage acts as a
gate" — about_project.md). See
docs/architecture/service-boundaries.md#job-matching-service.

Ownership: `JobMatch` (docs/architecture/domain-model.md#jobmatch); narrow
update right on `Job.processing_status`.
Owned tables: `job_matches`; `jobs.processing_status` (update only).
APIs: `/jobs`, `/jobs/{job_id}/matches` (both read-only).
Kafka consumed: `jobs.discovered`, `profiles.updated`.
Kafka produced: `jobs.matched`, `jobs.shortlisted`, `contacts.requested`.
Dependencies: Resume/Profile Service (read ResumeProfile list via API), LLM
Provider Layer (semantic scoring).

Explicitly outside this component's responsibility: finding contacts,
generating outreach, or tracking lifecycle state beyond its own JobMatch
record — all downstream, triggered by the events this service produces,
never called directly.
"""
