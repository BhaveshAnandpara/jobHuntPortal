"""Tracking Service — the single source of truth for the user-facing
opportunity lifecycle. Aggregates events from every other component into
one Application record per job; never initiates work in other components.
See docs/architecture/service-boundaries.md#tracking-service.

Ownership: `Application`, `ApplicationHistory`
(docs/architecture/domain-model.md).
Owned tables: `applications`, `application_history`.
APIs: `/applications`, `/applications/{id}`, `/applications/{id}/status`.
Kafka consumed: `jobs.discovered`, `jobs.matched`, `jobs.shortlisted`,
`contacts.found`, `outreach.generated`, `outreach.approved`,
`outreach.sent` (all seven upstream topics).
Kafka produced: `applications.updated`.
Dependencies: none (deliberately) — Tracking never calls another
component's API. This is what lets it safely consume from every other
component without creating a dependency cycle back onto them — see
docs/architecture/dependency-graph.md#no-circular-dependencies.
"""
