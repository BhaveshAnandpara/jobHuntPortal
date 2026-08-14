"""Job Ingestion Service (manual URL path) + Job Discovery Service
(automatic path). Both converge on the identical `Job` schema and
`jobs.discovered` topic but share no code — see
docs/architecture/service-boundaries.md#job-ingestion-service and
#job-discovery-service.

Ownership: `Job` (create) — Job Ingestion Service inserts rows with
source_type=MANUAL_URL, Job Discovery Service inserts rows with any other
source_type. `JobSource` is owned solely by Job Discovery Service. This is
the one shared-write table in the system — see
docs/architecture/ownership.md#shared-write-jobs-table.
Owned tables: `jobs` (insert, partitioned by source_type), `job_sources`.
"""
