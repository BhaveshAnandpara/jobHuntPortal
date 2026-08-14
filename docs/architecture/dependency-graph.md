# Component Dependency Graph

Five separate dependency relations. A component pair can be connected by
more than one relation type (e.g. Job Matching Service both calls
Resume/Profile Service's API *and* consumes its `profiles.updated` event) —
list each relation type explicitly rather than collapsing them into one
generic arrow, since they have different failure/coupling characteristics.

## 1. Compile/import dependencies

Every component imports from `shared/` (types, enums, error codes, event
envelope) and from `infrastructure/` (Kafka client, DB session, LLM client)
as needed. **No component imports another component's module.** This is
absolute — it is the one dependency type with zero exceptions anywhere in
this architecture.

```
users/, profiles/, jobs/, matching/, contacts/, outreach/, tracking/
                    │
                    ▼ (all of them import, never each other)
        shared/{types,enums,errors,events}
                    │
                    ▼
     infrastructure/{kafka,database,llm,external}
```

## 2. Runtime API dependencies

Synchronous HTTP calls between components. Kept minimal — most
cross-component needs are satisfied by Kafka events instead (see relation
3). Arrows point from caller to callee.

```
Job Discovery Service ──► User Service            (read UserPreferences)
Job Discovery Service ──► Resume/Profile Service   (read ResumeProfile list)
Job Matching Service  ──► Resume/Profile Service   (read ResumeProfile list)
Job Matching Service  ──► User Service             (GET /users/{id}/preferences, read UserPreferences)
Outreach Service       ──► Job Matching Service     (GET /jobs/{job_id}/matches, read
                                                      selected_profile_id/selected_resume_id)
Outreach Service       ──► Resume/Profile Service   (read selected resume/profile, by the
                                                      profile_id obtained from the call above)
Outreach Service       ──► Job Ingestion Service    (GET /jobs/{job_id}, read company/title
                                                      for message personalization — neither
                                                      ContactRankingResult nor JobMatchResult
                                                      carries them)
Resume/Profile Service ──► User Service            (HEAD /users/{id}, validate user_id exists)
Job Ingestion Service  ──► User Service            (HEAD /users/{id}, validate user_id exists)
```

Both `HEAD /users/{id}` edges were added to resolve a documented Wave 1
foundation gap: `POST /resumes` and `POST /jobs/ingest-url` each require
"user_id exists" validation (see `component-contracts.md`), but no contract
previously existed for a caller to check it without a direct table read
(forbidden by `ownership.md`). Both edges terminate at User Service, which
itself makes no outbound calls (see below), so neither introduces a cycle.

The `Outreach Service ──► Job Matching Service` edge resolves a Wave 3
foundation gap of the same shape: `component-contracts.md`'s Outreach
Service `contacts.found` consumer entry has always said it fetches "the
job's selected CandidateProfile/Resume ... using `selected_resume_id` from
the job's `JobMatchResult`, retrieved via Job Matching Service's read API"
— but `ContactRankingResult` (the `contacts.found` payload) carries no
`selected_resume_id`/`selected_profile_id` field, and this document
previously listed only the second half of that chain (Outreach Service →
Resume/Profile Service), never the first. The full two-hop chain is:
`GET /jobs/{job_id}/matches` (Job Matching Service, returns
`JobMatchResponse` with `selected_profile_id`/`selected_resume_id`) →
`GET /profiles/{profile_id}` (Resume/Profile Service, returns the actual
`ResumeProfile`). This cannot introduce a cycle: Job Matching Service's own
outbound relations terminate at Resume/Profile Service and User Service
(both already acyclic per this section), and Job Matching Service consumes
no event and calls no API that Outreach Service produces/exposes — nothing
loops back.

The `Outreach Service ──► Job Ingestion Service` edge closes a third,
related gap found in the same Wave 3 implementation pass: personalizing an
outreach message needs the job's actual `company`/`title`, but neither
`ContactRankingResult` (`contacts.found`'s payload, which Outreach Service
consumes) nor `JobMatchResponse` (fetched via the edge above) carries them
— `NormalizedJob` is the only canonical type that does, and it never
crosses this far downstream. `GET /jobs/{job_id}` (added to Job Ingestion
Service in this same pass — see `api-contracts.md`'s note on it) is the
correct, already-designated read path for a full job's canonical detail;
no new endpoint or type was introduced. Acyclic for the same reason as the
`HEAD /users/{id}` edges: Job Ingestion Service originates zero outbound
relations of its own toward Outreach Service (its only outbound call is to
User Service, per this section, for the unrelated `user_id`-exists check).

Tracking Service and Contact Discovery Service make **no** runtime API
calls to other platform components (Contact Discovery Service's
`contacts.requested` payload already carries everything it needs —
`company`, `title`, `location` — without a callback). Job Ingestion Service
makes exactly one, to User Service, for the existence check above; it still
calls no other platform component (page fetch/extraction go through the
External Integrations and LLM Provider infrastructure layers, not a
business component's API — see relation 5 below).

## 3. Kafka/event dependencies

Asynchronous, decoupled — the producer does not know or care who consumes,
and the consumer does not block the producer. Arrows point from producer to
consumer (i.e., "consumer depends on producer's output").

```
Job Ingestion Service   ──jobs.discovered──►     Job Matching Service, Tracking Service
Job Discovery Service   ──jobs.discovered──►     Job Matching Service, Tracking Service
Resume/Profile Service  ──profiles.updated──►    Job Matching Service
Job Matching Service    ──jobs.matched──►        Tracking Service
Job Matching Service    ──jobs.shortlisted──►    Tracking Service
Job Matching Service    ──contacts.requested──►  Contact Discovery Service
Contact Discovery Svc   ──contacts.found──►      Outreach Service, Tracking Service
Outreach Service        ──outreach.generated──►  Tracking Service
Outreach Service        ──outreach.approved──►   Outreach Service (send worker), Tracking Service
Outreach Service        ──outreach.sent──►       Tracking Service
Tracking Service        ──applications.updated──► (reserved: future Analytics)
```

Full per-topic detail in [kafka-topics.md](kafka-topics.md).

## 4. Database dependencies

Every component depends only on the tables it owns (see
[database-ownership.md](database-ownership.md)), plus read access to the
shared `workflow_executions` table for observability. No component queries
another component's tables directly — cross-component data needs are
satisfied by relation 2 or 3 instead.

Every component's own `repository.py` additionally depends on the shared
Database Infrastructure layer (`infrastructure/database/`, owned by the
Database Agent — see
[ownership.md#infrastructure-ownership-non-business](ownership.md#infrastructure-ownership-non-business))
for its SQLAlchemy engine/session and Alembic migration tooling. This is a
shared *infrastructure* dependency, the same category as the LLM Provider
Layer in relation 5 below, not a business dependency — it never includes a
business table's schema, which stays owned by each component per
[database-ownership.md](database-ownership.md).

```
each component ──owns──► its own tables (database-ownership.md)
each component ──reads──► workflow_executions (shared, observability only)
each component's repository.py ──depends on──► Database Infrastructure
                                                (infrastructure/database/,
                                                engine/session/migrations only)
```

The one shared-write exception (`jobs`, written by both Job Ingestion
Service and Job Discovery Service, narrowly updated by Job Matching
Service) is documented in
[ownership.md#shared-write-jobs-table](ownership.md#shared-write-jobs-table)
and is not a dependency between those components on each other — both write
disjoint `source_type` partitions and neither reads the other's rows to
decide what to write.

## 5. LLM/tool dependencies

```
Resume/Profile Service     ──► LLM Provider Layer
Job Ingestion Service      ──► LLM Provider Layer, External Integrations Layer (page fetch)
Job Discovery Service      ──► LLM Provider Layer, External Integrations Layer (job board search)
Job Matching Service       ──► LLM Provider Layer
Contact Discovery Service  ──► LLM Provider Layer, External Integrations Layer (people-search)
Outreach Service           ──► LLM Provider Layer, External Integrations Layer (email/LinkedIn send)
Tracking Service           ──► (none)
```

`LLM Provider Layer`, `External Integrations Layer` (owned by the External
Integrations Agent — see
[ownership.md#infrastructure-ownership-non-business](ownership.md#infrastructure-ownership-non-business)),
`Database Infrastructure`, and `Kafka Infrastructure` are infrastructure,
not business components — everything relevant may depend on them; they
depend on nothing in `shared/` or any business component (see
[service-boundaries.md](service-boundaries.md#llm-provider-layer),
[service-boundaries.md](service-boundaries.md#external-integrations-layer),
[service-boundaries.md](service-boundaries.md#database-infrastructure)).

## Composite diagram

The primary causal chain (about_project.md's End-to-End flow), annotated
with which relation type carries each arrow. `[K]` = Kafka event, `[A]` =
runtime API call.

```
Resume/Profile Service
        │
        │ [K] profiles.updated
        ▼
                                Job Ingestion Service ──┐
                                                         │ [K] jobs.discovered
                                Job Discovery Service ───┤
        [A] read profiles ◄─────────────────────────────┤
        [A] read preferences ◄── Job Discovery Service   │
                                                         ▼
                                              Job Matching Service
                                                         │
                                    ┌────────────────────┼────────────────────┐
                             [K] jobs.matched      [K] jobs.shortlisted  [K] contacts.requested
                                    │                    │                    │
                                    ▼                    ▼                    ▼
                              Tracking Service ◄──────────────────  Contact Discovery Service
                                    ▲                                        │
                                    │                              [K] contacts.found
                                    │                                        │
                                    │                    ┌───────────────────┤
                                    │                    ▼                    ▼
                                    │           Outreach Service      Tracking Service
                                    │                    │
                        [K] outreach.generated/approved/sent
                                    │
                                    ▼
                              Tracking Service
                                    │
                          [K] applications.updated
                                    ▼
                          (reserved: future Analytics)
```

`Job Discovery ──► Job Matching` is indirect (via `jobs.discovered`), not a
direct arrow — matching this document's earlier examples of same shape.

## No circular dependencies

Walking every relation type above: **Tracking Service** is the only
component every other component's output reaches (directly or
transitively), and Tracking Service produces no API and makes no runtime
call back into any of them — it only publishes `applications.updated`,
which nothing upstream of it consumes. This is what makes it safe for
Tracking Service to depend on (consume from) everyone without creating a
cycle.

**User Service** is the other component worth walking explicitly: it is
now called by Resume/Profile Service, Job Discovery Service, Job
Ingestion Service, and Job Matching Service (relation 2), but User Service
itself originates zero
outbound relations of any type in this document — no API call, no Kafka
produce/consume, no LLM/external dependency. Every edge involving it is
inbound, so it cannot participate in a cycle no matter how many callers it
gains.

The one relationship that was deliberately **rejected** during this
architecture pass for exactly this reason: Contact Discovery Service
consuming Outreach Service's `outreach.sent` (to mark a contact
"contacted") would have closed a cycle with Outreach Service's existing
consumption of Contact Discovery Service's `contacts.found`
(`Contact Discovery → Outreach → Contact Discovery`). See
[kafka-topics.md](kafka-topics.md) and
[state-machines.md](state-machines.md#contact-lifecycle-contactstatus--contactstatus)
for the resolution (derive "contacted" from `Outreach` records instead of
storing it on `Contact`).

No other pair of components has bidirectional relations of the same type,
so no other cycle exists. If a future change proposes a new API call or
Kafka consumption relationship, check it against this diagram first — if
tracing the new arrow plus existing arrows leads back to the component that
originated the chain, redesign it (e.g. by deriving the data instead of
subscribing to it, as done here) rather than accepting the cycle.
