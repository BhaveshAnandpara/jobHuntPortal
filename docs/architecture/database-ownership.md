# Database Ownership

One PostgreSQL database (see [overview.md#deployment-model](overview.md#deployment-model)).
Table ownership is enforced by code convention: each table's SQLAlchemy
model and repository live under that owning component's module (see
[repository-structure.md](repository-structure.md)), and no other
component's code may import that repository or issue raw SQL against the
table.

"May create" / "may update" below are stricter than "owns" for `jobs`, which
has two writers — see the note under that table.

The shared SQLAlchemy engine/session lifecycle and Alembic migration
tooling that every component's `repository.py` runs on top of live in
`infrastructure/database/`, owned by the Database Agent — see
[ownership.md#infrastructure-ownership-non-business](ownership.md#infrastructure-ownership-non-business)
and
[service-boundaries.md#database-infrastructure](service-boundaries.md#database-infrastructure).
That is purely connection/migration-framework plumbing; it never includes
a business table's schema, and every table's ownership below is unchanged
by it.

## `users`

| | |
|---|---|
| Entity | `User` |
| Owner | User Service |
| Primary key | `id` (`UserId`) |
| Relationships | referenced by `user_id` FK-equivalent in nearly every other table (logical FK; not necessarily a DB-enforced cross-schema FK if tables are later split) |
| May create | User Service |
| May update | User Service |
| May read | all components (read-only, for display/denormalization at write time) |
| Produces domain events | no |

## `user_preferences`

| | |
|---|---|
| Entity | `UserPreferences` |
| Owner | User Service |
| Primary key | `id` |
| Relationships | 1:1 `users.id` |
| May create | User Service |
| May update | User Service |
| May read | Job Discovery Service, Job Matching Service (via API, not direct query) |
| Produces domain events | no |

## `resumes`

| | |
|---|---|
| Entity | `Resume` |
| Owner | Resume/Profile Service |
| Primary key | `id` |
| Relationships | `user_id` -> `users.id`; 1:1 with a `candidate_profiles` row |
| May create | Resume/Profile Service |
| May update | Resume/Profile Service |
| May read | all components, via API |
| Produces domain events | indirectly — `profiles.updated` is emitted from the parsing workflow that reads this table, not directly from writes to it |

## `candidate_profiles`

| | |
|---|---|
| Entity | `CandidateProfile` |
| Owner | Resume/Profile Service |
| Primary key | `id` |
| Relationships | `resume_id` -> `resumes.id`, `user_id` -> `users.id`; referenced by `job_matches.selected_profile_id` |
| May create | Resume/Profile Service |
| May update | Resume/Profile Service |
| May read | Job Matching Service (via API, assembled as `ResumeProfile`) |
| Produces domain events | yes — `ProfileUpdatedEvent` on create/archive |

## `job_sources`

| | |
|---|---|
| Entity | `JobSource` |
| Owner | Job Discovery Service |
| Primary key | `id` |
| Relationships | `user_id` -> `users.id`; parent of `jobs` rows where `source_id` is set |
| May create | Job Discovery Service |
| May update | Job Discovery Service |
| May read | Job Discovery Service only |
| Produces domain events | no |

## `jobs`

| | |
|---|---|
| Entity | `Job` |
| Owner | Job Ingestion Service (manual rows) + Job Discovery Service (automatic rows) — see note |
| Primary key | `id` |
| Relationships | `user_id` -> `users.id`; optional `source_id` -> `job_sources.id`; referenced by `job_matches.job_id`, `applications.job_id`, `contacts.job_id`, `outreach.job_id` |
| May create | Job Ingestion Service (`source_type=MANUAL_URL`), Job Discovery Service (all other `source_type`) |
| May update | Job Matching Service — `processing_status` column only |
| May read | all components, via API or the `NormalizedJob` event payload |
| Produces domain events | yes — `JobDiscoveredEvent` at creation time |

**Note — the one shared-write table.** `jobs` is the sole table with two
creating components. This is intentional (both ingestion paths must produce
the identical downstream shape) and narrowly scoped: each writer only
inserts rows for its own `source_type`, and Job Matching Service's update
right is limited to one column. No component ever updates a `jobs` row it
did not create, except that single documented column grant. See
[ownership.md#shared-write-jobs-table](ownership.md#shared-write-jobs-table).

**`processing_status` transition ownership.** This column's full lifecycle
is documented in
[state-machines.md#job-processing-lifecycle](state-machines.md#job-processing-lifecycle);
the summary, since it was previously a source of ambiguity between this
document and `component-contracts.md`'s Job Ingestion Service entry:

| Transition | Component | Mechanism |
|---|---|---|
| (none) → `NORMALIZED` | Job Ingestion Service | sets the initial value on `INSERT`, `source_type=MANUAL_URL`, only after extraction succeeds synchronously — never a separate `UPDATE` |
| (none) → `NORMALIZED` | Job Discovery Service | sets the initial value on `INSERT`, any other `source_type`, per discovered posting |
| `NORMALIZED` → `MATCHED` \| `FAILED` | Job Matching Service | the one and only `UPDATE` of this column performed by any component, per the "May update" row above |

Setting a column's value at `INSERT` time is not a write to an existing
row and is therefore not part of the "May update" grant — it is covered by
"May create" instead. `PENDING` remains defined in the shared
`JobProcessingStatus` enum (`shared-types.md#shared-enums`) for a possible
future asynchronous-ingestion variant, but no current component sets it;
Job Ingestion Service's `POST /jobs/ingest-url` is synchronous-only (see
`component-contracts.md#job-ingestion-service`). Introducing a real
asynchronous path in a future revision would require its own narrowly
scoped `UPDATE` grant here — it cannot be added by Job Ingestion Service
unilaterally reusing Job Matching Service's existing one.

## `job_matches`

| | |
|---|---|
| Entity | `JobMatch` |
| Owner | Job Matching Service |
| Primary key | `id` |
| Relationships | `job_id` -> `jobs.id`, `selected_profile_id` -> `candidate_profiles.id`, `selected_resume_id` -> `resumes.id` |
| May create | Job Matching Service |
| May update | Job Matching Service — append-only for the analytical fields (re-matching inserts a new row, never mutates one), plus one narrow permitted update per row: `published_at`, see "Publish-reliability column" below |
| May read | Tracking Service, via `JobMatchResult` event payload only (never queries this table directly) |
| Produces domain events | yes — `JobMatchedEvent`, and `JobShortlistedEvent` when applicable |

### Publish-reliability column (`published_at`)

**The gap this closes.** `persist_and_publish` (`langgraph-state.md#jobmatchingstate`)
does a database write (`INSERT job_matches`, `UPDATE jobs.processing_status`)
followed by a separate Kafka publish (`jobs.matched`, optionally
`jobs.shortlisted`). These are not atomic: the DB write can commit and the
publish can still fail (broker unavailable, network partition). Without a
fix, the `jobs.discovered` consumer's own idempotency check ("does a
`JobMatch` already exist for this `job_id`? if so, skip") would treat the
row's mere existence as proof the whole job was handled — including the
publish — and skip every future attempt, silently losing the event
forever. `jobs.matched`/`jobs.shortlisted` are exactly what Tracking
Service (and, once it exists, Contact Discovery Service via
`contacts.requested`) depend on to ever learn a job was matched, so this
gap was worth closing before either of those components starts.

**Decision: a one-column marker on `job_matches`, not a transactional
outbox table.** A full outbox (a separate `event_outbox` table + an
independent publisher/sweeper process) is the textbook fix for this class
of dual-write problem, and was seriously considered. It was rejected as
more machinery than this system's existing guarantees call for:

- The platform's Kafka Infrastructure already provides in-band retry (3
  attempts, exponential backoff — `kafka-topics.md`'s "Retry strategy")
  for exactly this kind of transient failure, *as long as the handler
  actually retries the failing step instead of short-circuiting past it*.
  The real defect was the idempotency check swallowing a real failure as a
  false "already done", not an absence of a retry mechanism.
- Beyond the retry budget, the platform's already-approved terminal
  mechanism is the dead-letter topic plus manual/operator reprocessing
  (`kafka-topics.md`: "DLQ messages require manual/operator
  reprocessing — no automatic DLQ replay is implemented"). A full outbox's
  independent background sweeper would retry indefinitely without operator
  involvement — a *stronger* guarantee than every other event in this
  system already gets, which would make Job Matching an inconsistent
  special case rather than fixing a real deficiency relative to the rest
  of the platform.
- A dead-lettered `jobs.discovered` message, once reprocessed (manually
  republished onto `jobs.discovered`), re-enters `handle_job_discovered`
  and — with the fix below — correctly resumes from "publish only", not
  from a full re-match. The existing DLQ path is therefore a sufficient
  recovery mechanism once the idempotency check is fixed to stop masking
  the failure; no new infrastructure component is needed to reach
  equivalent end-to-end reliability.

**The fix.** `job_matches` gains one nullable column:

| Column | Type | Meaning |
|---|---|---|
| `published_at` | `datetime \| None` | `NULL` until `jobs.matched` (and `jobs.shortlisted`, when applicable) have both been published successfully; set once, never cleared |

`handle_job_discovered`'s idempotency check becomes three-way instead of
two-way:

```
no JobMatch row for this job_id
    → run the full workflow (score, select, persist with published_at=NULL,
      publish, then set published_at)

JobMatch row exists, published_at IS NULL
    → do not re-score (avoid redundant LLM calls for work already done);
      re-publish jobs.matched (+ jobs.shortlisted if recommendation ==
      SHORTLIST) from the already-persisted JobMatchResult, then set
      published_at

JobMatch row exists, published_at IS NOT NULL
    → true duplicate redelivery of an already-fully-handled message; skip,
      exactly as before
```

This is the same "business transaction, then a separate confirmed publish
step" shape the outbox pattern would have provided (`persist` and
`publish` remain distinguishable states), just without a second table or a
standing background process — the existing Kafka consumer retry loop and
DLQ are the delivery mechanism for the publish step, not a bespoke one.
`published_at` is only ever set forward (`NULL` → a timestamp), matching
the append-only spirit of this table; nothing here reopens or re-derives
the analytical fields (`match_score`, `matched_skills`, etc.) once written.

**Idempotent publish.** Kafka's platform-wide delivery semantics are
already "at-least-once, consumers must be idempotent"
(`kafka-topics.md`'s delivery semantics). A retry that re-publishes
`jobs.matched`/`jobs.shortlisted` after a prior attempt's publish actually
succeeded but the `published_at` write failed (a narrower crash window
than the original gap) produces a duplicate message — already within every
downstream consumer's existing tolerance, not a new burden introduced by
this fix.

## `contacts`

| | |
|---|---|
| Entity | `Contact` |
| Owner | Contact Discovery Service |
| Primary key | `id` |
| Relationships | `job_id` -> `jobs.id`, `user_id` -> `users.id`; referenced by `contact_rankings.contact_id`, `outreach.contact_id`, `applications.referral_contact_id` |
| May create | Contact Discovery Service |
| May update | Contact Discovery Service |
| May read | Outreach Service, Tracking Service — via `ContactRankingResult` event payload, not direct query |
| Produces domain events | yes — as part of `ContactsFoundEvent` |

## `contact_rankings`

| | |
|---|---|
| Entity | `ContactScore` |
| Owner | Contact Discovery Service |
| Primary key | `id` |
| Relationships | `contact_id` -> `contacts.id`, `job_id` -> `jobs.id` |
| May create | Contact Discovery Service |
| May update | Contact Discovery Service |
| May read | Contact Discovery Service only (score breakdown is internal detail; only the final `relevance_score` leaves via the event payload) |
| Produces domain events | no (folded into `ContactsFoundEvent`, not published independently) |

## `outreach`

| | |
|---|---|
| Entity | `Outreach` |
| Owner | Outreach Service |
| Primary key | `id` |
| Relationships | `job_id` -> `jobs.id`, `contact_id` -> `contacts.id`, `user_id` -> `users.id` |
| May create | Outreach Service |
| May update | Outreach Service (including the human approval and send-worker updates — both are internal to this service) |
| May read | Tracking Service, via event payloads only |
| Produces domain events | yes — `OutreachGeneratedEvent`, `OutreachApprovedEvent`, `OutreachSentEvent` |

## `applications`

| | |
|---|---|
| Entity | `Application` |
| Owner | Tracking Service |
| Primary key | `id` |
| Relationships | `job_id` -> `jobs.id` (1:1), `user_id` -> `users.id`, `selected_resume_id` -> `resumes.id`, `referral_contact_id` -> `contacts.id` |
| May create | Tracking Service (on first event observed for a `job_id`) |
| May update | Tracking Service only — including updates that originate from a user's manual status change, which go through Tracking's own API |
| May read | any component (this is the platform's primary read model / dashboard source) |
| Produces domain events | yes — `ApplicationUpdatedEvent` on every status change |

## `application_history`

| | |
|---|---|
| Entity | `ApplicationHistory` |
| Owner | Tracking Service |
| Primary key | `id` |
| Relationships | `application_id` -> `applications.id` |
| May create | Tracking Service (insert-only, one row per transition) |
| May update | nobody — append-only |
| May read | any component |
| Produces domain events | no — this table is itself the durable trail; it does not additionally trigger events |

## Shared observability table

### `workflow_executions`

| | |
|---|---|
| Entity | `WorkflowExecution` |
| Owner | shared, **row-level ownership** by `workflow_type` |
| Primary key | `id` |
| Relationships | polymorphic reference via `entity_ref_id` (no DB-enforced FK — points at a `Job`, `Contact`, or `Outreach` id depending on `workflow_type`) |
| May create | Job Matching Service (`workflow_type=JOB_MATCHING`), Contact Discovery Service (`workflow_type=CONTACT_DISCOVERY`), Outreach Service (`workflow_type=OUTREACH_GENERATION`) |
| May update | only the component that created the row (matched by `workflow_type`) |
| May read | any component (observability/debugging) |
| Produces domain events | no — never published to Kafka |

This is the one documented exception to "one table, one writer." It is
purely observability data (workflow run status, not business state), and
splitting it into three separate per-service tables would only add
boilerplate without changing any access pattern, since no component ever
needs to join across another component's `WorkflowExecution` rows for
business logic — only for debugging/tracing by `correlation_id`.

## Prevented pattern

No entity above is independently persisted by more than one component
(other than the two documented, narrowly-scoped exceptions: `jobs` and
`workflow_executions`). If a future component believes it needs its own copy
of, say, `Contact` data, that is a sign it should be consuming
`ContactsFoundEvent` or calling the Contact Discovery Service's read API
instead of creating a second table.
