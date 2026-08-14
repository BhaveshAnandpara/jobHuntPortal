"""Resume/Profile Service — turns uploaded resumes into structured,
profession-agnostic candidate profiles.

Ownership: `Resume`, `CandidateProfile` (docs/architecture/domain-model.md).
Owned tables: `resumes`, `candidate_profiles`
(docs/architecture/database-ownership.md).
APIs: `/resumes`, `/profiles` (docs/architecture/api-contracts.md#resumeprofile-service).
Kafka produced: `profiles.updated`. Kafka consumed: none.
Dependencies: LLM Provider Layer (resume parsing/extraction).

This is the component most directly responsible for the platform's
"generic, not hard-coded to software engineering" goal — no
profession-specific extraction logic; skills/roles/industries are all
free-text/list fields inferred per resume (about_project.md,
docs/architecture/service-boundaries.md#resumeprofile-service).

Explicitly outside this component's responsibility: deciding whether a
profile is a good fit for any specific job (Job Matching Service).
"""
