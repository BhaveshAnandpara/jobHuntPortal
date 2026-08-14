# Ownership Boundaries

This is the master ownership matrix. [service-boundaries.md](service-boundaries.md)
gives the narrative version (responsibility, dependencies, explicit
non-goals) per component; this document is the flat lookup table so any two
agents can quickly settle "who owns X."

**Rule:** a component may only write to the entities/tables it owns below.
Everything else it needs from another component's data comes through that
component's API or a Kafka event it consumes — never a direct write, and
never a direct cross-component table read in application code (reporting/
analytics tooling is exempt, since it is read-only by construction).

## Component → owned entities

| Component | Owned domain entities |
|---|---|
| User Service | `User`, `UserPreferences` |
| Resume/Profile Service | `Resume`, `CandidateProfile` |
| Job Ingestion Service | `Job` (create, manual path) |
| Job Discovery Service | `Job` (create, automatic path), `JobSource` |
| Job Matching Service | `JobMatch`; narrow update rights on `Job.processing_status` |
| Contact Discovery Service | `Contact`, `ContactScore` |
| Outreach Service | `Outreach` |
| Tracking Service | `Application`, `ApplicationHistory` |
| (shared, row-owned) | `WorkflowExecution` — see [database-ownership.md#shared-observability-table](database-ownership.md#shared-observability-table) |

### Shared-write `jobs` table

`Job` is the one entity two components both create into (Job Ingestion for
manual URLs, Job Discovery for automatic search), because about_project.md
requires both paths to converge on the identical downstream shape. This is
the **only** table with more than one writer for `INSERT`, and it is
explicitly scoped:

- Job Ingestion Service: `INSERT` only, `source_type = MANUAL_URL`.
- Job Discovery Service: `INSERT` only, `source_type != MANUAL_URL`.
- Job Matching Service: `UPDATE processing_status` only, on rows it has
  finished matching. No other column, no other component.

No component ever updates another component's `Job` rows outside these
explicit grants.

## Component → owned database tables

See [database-ownership.md](database-ownership.md) for full column-level
detail. Summary:

| Component | Tables |
|---|---|
| User Service | `users`, `user_preferences` |
| Resume/Profile Service | `resumes`, `candidate_profiles` |
| Job Ingestion Service | `jobs` (insert, manual) |
| Job Discovery Service | `jobs` (insert, automatic), `job_sources` |
| Job Matching Service | `job_matches`; `jobs.processing_status` (update only) |
| Contact Discovery Service | `contacts`, `contact_rankings` |
| Outreach Service | `outreach` |
| Tracking Service | `applications`, `application_history` |
| shared (row-owned) | `workflow_executions` |

## Component → Kafka topics produced

| Component | Produces |
|---|---|
| Job Ingestion Service | `jobs.discovered` |
| Job Discovery Service | `jobs.discovered` |
| Resume/Profile Service | `profiles.updated` |
| Job Matching Service | `jobs.matched`, `jobs.shortlisted`, `contacts.requested` |
| Contact Discovery Service | `contacts.found` |
| Outreach Service | `outreach.generated`, `outreach.approved`, `outreach.sent` |
| Tracking Service | `applications.updated` |

## Component → Kafka topics consumed

| Component | Consumes |
|---|---|
| Job Matching Service | `jobs.discovered`, `profiles.updated` |
| Contact Discovery Service | `contacts.requested` |
| Outreach Service | `contacts.found`, `outreach.approved` (own event, separate consumer group for the send worker) |
| Tracking Service | `jobs.discovered`, `jobs.matched`, `jobs.shortlisted`, `contacts.found`, `outreach.generated`, `outreach.approved`, `outreach.sent` |

Full per-topic detail (partition keys, ordering, delivery semantics, DLQ) is
in [kafka-topics.md](kafka-topics.md).

## Component → APIs exposed

| Component | APIs |
|---|---|
| User Service | `/users`, `/users/{id}/preferences`, `HEAD /users/{id}` (existence check) |
| Resume/Profile Service | `/resumes`, `/profiles` |
| Job Ingestion Service | `/jobs/ingest-url`, `GET /jobs/{job_id}` |
| Job Discovery Service | `/job-sources` |
| Job Matching Service | `/jobs/{job_id}/matches` (read-only) |
| Contact Discovery Service | `/jobs/{job_id}/contacts`, `/jobs/{job_id}/contacts/search` |
| Outreach Service | `/outreach`, `/outreach/{id}/approve`, `/outreach/{id}/reject`, `/outreach/{id}/edit` |
| Tracking Service | `/applications`, `/applications/{id}`, `/applications/{id}/status` |

Full request/response contracts are in [api-contracts.md](api-contracts.md).

## Component → external tools/APIs it may call

| Component | External dependency |
|---|---|
| Resume/Profile Service | LLM Provider Layer (resume parsing/extraction) |
| Job Ingestion Service | External Integrations Layer (Playwright/BeautifulSoup page fetch), LLM Provider Layer (extraction) |
| Job Discovery Service | External Integrations Layer (job board search APIs/scrapers), LLM Provider Layer |
| Job Matching Service | LLM Provider Layer (semantic scoring) |
| Contact Discovery Service | External Integrations Layer (people-search APIs/tools), LLM Provider Layer |
| Outreach Service | LLM Provider Layer (message generation), External Integrations Layer (email/LinkedIn send providers) |
| Tracking Service | none |

No component other than the ones listed above calls an LLM directly — all
LLM access goes through the shared LLM Provider Layer (see
[service-boundaries.md](service-boundaries.md#llm-provider-layer)) so
prompts, model selection, and rate limiting are centralized. Likewise, no
component performs a raw HTTP/browser-automation call to a third-party,
non-platform service directly — all Playwright/BeautifulSoup, job-board
search, people-search, and email/LinkedIn send calls go through the shared
External Integrations Layer (see
[service-boundaries.md](service-boundaries.md#external-integrations-layer),
owned by the External Integrations Agent) so auth, retries, and rate
limiting for third-party integrations are centralized in one place. The
*business decision* of when/how to call these adapters (which URL to
fetch, which search query to run, what message to send, whether to send at
all) remains with the owning component — the External Integrations Agent
owns only the reusable client/adapter code, never job discovery, contact
ranking, or outreach decision logic.

## Component → shared contracts it depends on

Every component depends on `shared/types/ids.py`, `shared/types/enums.py`,
`shared/errors/codes.py`, and `shared/events/envelope.py` unconditionally.
Beyond that:

| Component | Additional shared types it consumes |
|---|---|
| Job Matching Service | `NormalizedJob`, `ResumeProfile`, `UserPreferences`, `JobMatchResult`, `JobMatchingState` |
| Contact Discovery Service | `JobMatchResult` (subset), `ContactCandidate`, `ContactRankingResult`, `ContactDiscoveryState` |
| Outreach Service | `ContactRankingResult`, `OutreachDraft`, `OutreachGenerationState` |
| Tracking Service | every event payload type (it is the universal downstream consumer) |

## Component → components it may communicate with

Directional, matching [dependency-graph.md](dependency-graph.md). "May
communicate with" means via API call and/or Kafka event only — never via
shared module import of business logic.

| Component | May call (API) | May consume events from |
|---|---|---|
| Resume/Profile Service | User Service (`HEAD /users/{id}`, validate user_id exists) | — |
| Job Ingestion Service | User Service (`HEAD /users/{id}`, validate user_id exists) | — |
| Job Discovery Service | Resume/Profile Service (read profiles), User Service (read preferences) | — |
| Job Matching Service | Resume/Profile Service (read profiles), User Service (read preferences) | Job Ingestion, Job Discovery, Resume/Profile |
| Contact Discovery Service | — | Job Matching |
| Outreach Service | Job Matching Service (`GET /jobs/{job_id}/matches`, read selected_profile_id/selected_resume_id), Resume/Profile Service (read selected resume/profile for tone), Job Ingestion Service (`GET /jobs/{job_id}`, read company/title for personalization) | Contact Discovery, itself (`outreach.approved`) |
| Tracking Service | — (read-only consumer) | Job Ingestion, Job Discovery, Job Matching, Contact Discovery, Outreach |

No component listed as a producer above ever calls the Tracking Service's
API to push state — Tracking pulls its picture of the world entirely from
events. This is what keeps Tracking able to consume from every other
component without creating a dependency back onto it (see
[dependency-graph.md](dependency-graph.md)).

## Infrastructure ownership (non-business)

Two infrastructure boundaries exist alongside the seven business
components and the Kafka/LLM infrastructure already covered above (see
[service-boundaries.md](service-boundaries.md#kafka-infrastructure) and
[service-boundaries.md](service-boundaries.md#llm-provider-layer)). Neither
owns a business entity, table, topic, or API — they exist so shared,
non-domain plumbing is centralized in one place instead of being
duplicated per component.

| Infrastructure layer | Owning agent | Lives in | Owns | Does not own |
|---|---|---|---|---|
| Database Infrastructure | Database Agent (`.claude/agents/database-agent.md`) | `infrastructure/database/` | SQLAlchemy engine/session management, connection pooling, the shared declarative Base, Alembic migration tooling/environment, DB health/connectivity utilities | Any business table's schema or columns — those remain owned by the component listed in [Component → owned database tables](#component--owned-database-tables) above and in [database-ownership.md](database-ownership.md). The Database Agent never writes a component's `models.py`. |
| External Integrations Layer | External Integrations Agent (`.claude/agents/external-integrations-agent.md`) | `infrastructure/external/` | Reusable third-party/external client adapters: the Playwright/BeautifulSoup page-fetch wrapper, job-board search client wrapper, the people-search API/tool client, the email/LinkedIn send provider client — auth, retries, rate limiting, and response-parsing primitives at the transport level | Job discovery logic, contact ranking, outreach decisions, or any other business logic. Those remain owned by Job Discovery Service (job-agent), Contact Discovery Service (contact-agent), and Outreach Service (outreach-agent) respectively — this layer only supplies the client each of them calls. |

This mirrors how `LLM Provider Layer` and `Kafka Infrastructure` are
already treated: infrastructure that every relevant component may depend
on, which itself depends on nothing in `shared/` or any business component
(see [dependency-graph.md](dependency-graph.md)). As with the shared-write
`jobs` table above, these are documented, narrowly-scoped exceptions to
"one component, one boundary" — not a precedent for further infrastructure
sprawl. A new infrastructure layer is an architecture change, not a local
decision (see [overview.md](overview.md#how-to-use-this-as-an-implementing-agent)).

## Rule: prefer a contract over reaching into internal state

If Component A needs Component B to do something, A does one of:

1. Call a documented API endpoint B exposes.
2. Produce an event B is documented to consume.
3. Read a shared type B already publishes.

A never imports B's internal modules, queries B's tables directly, or
depends on B's LangGraph state shape. This applies symmetrically — B has the
same obligation toward A.
