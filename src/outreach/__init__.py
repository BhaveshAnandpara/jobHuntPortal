"""Outreach Service — generates personalized outreach for the top-ranked
contact, gates it behind human approval, and sends it once approved. This
is where CLAUDE.md's "external outreach requires human approval" rule is
implemented as a real, auditable state transition.
See docs/architecture/service-boundaries.md#outreach-service.

Ownership: `Outreach` (docs/architecture/domain-model.md#outreach).
Owned tables: `outreach`.
APIs: `/outreach`, `/outreach/{id}/approve`, `/outreach/{id}/reject`,
`/outreach/{id}/edit`.
Kafka consumed: `contacts.found`, `outreach.approved` (own topic, dedicated
send-worker consumer group).
Kafka produced: `outreach.generated`, `outreach.approved`, `outreach.sent`.
Dependencies: Resume/Profile Service (read selected resume/profile via
API), LLM Provider Layer, external email/LinkedIn send providers.

Never sends anything without an APPROVED decision recorded first — a hard
product constraint (about_project.md "Non-Goal"), enforced by the state
machine in docs/architecture/state-machines.md#outreach-lifecycle, not
merely by convention.
"""
