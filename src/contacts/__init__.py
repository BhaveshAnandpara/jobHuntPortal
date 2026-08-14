"""Contact Discovery Service — given a shortlisted job, finds people at
the target company and ranks them by referral/outreach relevance.
See docs/architecture/service-boundaries.md#contact-discovery-service.

Ownership: `Contact`, `ContactScore` (docs/architecture/domain-model.md).
Owned tables: `contacts`, `contact_rankings`.
APIs: `/jobs/{job_id}/contacts`, `/jobs/{job_id}/contacts/search`.
Kafka consumed: `contacts.requested`. Kafka produced: `contacts.found`.
Dependencies: people-search APIs/tools, LLM Provider Layer.

The `ContactType` vocabulary this service uses must stay profession-generic
— no hard-coded "Software Engineer"/"Recruiter" logic branches.

Explicitly outside this component's responsibility: deciding *what* to say
to a contact (Outreach Service) or whether the job itself was a good match
(already decided upstream). Must not reintroduce the rejected
Contact <- outreach.sent circular dependency — see
docs/architecture/dependency-graph.md#no-circular-dependencies.
"""
