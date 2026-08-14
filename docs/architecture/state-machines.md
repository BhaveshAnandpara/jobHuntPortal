# State Transitions

Every enum used as a lifecycle status has exactly one valid state machine,
defined here, owned by exactly one component. No other component may write
a status value that skips a required transition or resurrects a terminal
state.

## Opportunity lifecycle (`Application.status` / `ApplicationStatus`)

This is the lifecycle from about_project.md's "Opportunity Lifecycle"
section, made precise. Owned entirely by **Tracking Service** — every
transition below is performed only by Tracking Service, in reaction to the
event named, never by the component that produced that event writing to
`applications` directly.

```
DISCOVERED
    │  jobs.discovered consumed (Application created)
    ▼
MATCHED ──────────────────────────► IGNORED  [terminal]
    │  jobs.matched consumed            ▲
    │  (recommendation=SHORTLIST/       │  jobs.matched consumed
    │   BORDERLINE)                     │  (recommendation=IGNORE)
    │                                   │  or manual dismiss (BORDERLINE)
    ▼
SHORTLISTED
    │  jobs.shortlisted consumed
    │  (Tracking immediately advances to CONTACT_SEARCH in the same
    │   handler — see note below)
    ▼
CONTACT_SEARCH
    │  contacts.found consumed, contacts non-empty
    ▼
CONTACT_FOUND
    │  outreach.generated consumed
    ▼
OUTREACH_GENERATED
    │  outreach.approved consumed
    ▼
OUTREACH_APPROVED
    │  outreach.sent consumed
    ▼
OUTREACH_SENT
    │  manual (user confirms a referral occurred)
    ▼
REFERRED
    │  manual
    ▼
APPLIED
    │  manual
    ▼
INTERVIEW
    │  manual                  │  manual
    ▼                          ▼
  OFFER [terminal]          REJECTED [terminal]
```

Additional edges (not shown in the linear chain above, all manual via
`PATCH /applications/{id}/status`):

| From | To | Trigger | Notes |
|---|---|---|---|
| any of `SHORTLISTED`, `CONTACT_SEARCH`, `CONTACT_FOUND`, `OUTREACH_GENERATED`, `OUTREACH_APPROVED`, `OUTREACH_SENT`, `REFERRED` | `APPLIED` | manual | "a user may apply directly without receiving a referral" — about_project.md |
| `DISCOVERED`, `MATCHED` | `APPLIED` | manual | user applies before the automated pipeline finishes |
| `APPLIED` | `REJECTED` | manual | rejection without interview |
| any non-terminal state | `WITHDRAWN` [terminal] | manual | user gives up on the opportunity |
| `CONTACT_SEARCH` (contacts empty) | stays `CONTACT_SEARCH` | `contacts.found` with `contacts == []` | not a transition — documented no-op, see [component-contracts.md](component-contracts.md#kafka-consumer-contactsfound) |

**Terminal states:** `OFFER`, `REJECTED`, `IGNORED`, `WITHDRAWN`. No
transition out of a terminal state is valid; a `PATCH` attempting one
returns `VALIDATION_ERROR`.

**Invalid transitions (non-exhaustive examples the API must reject):**
`DISCOVERED → INTERVIEW` (skips required matching), `INTERVIEW → DISCOVERED`
(backward), any transition out of `OFFER`/`REJECTED`/`IGNORED`/`WITHDRAWN`,
`MATCHED → OUTREACH_SENT` (skips contact/outreach steps without going
through the explicit `APPLIED` shortcut).

**Skippability:** every state from `SHORTLISTED` through `REFERRED` may be
skipped in favor of jumping straight to `APPLIED`, matching
about_project.md ("Not every opportunity must pass through every state").
No other skips are valid — the automated chain (`DISCOVERED` through
`OUTREACH_SENT`) only advances one event-driven step at a time.

**Note on `SHORTLISTED → CONTACT_SEARCH`:** these two statuses are recorded
as two separate `ApplicationHistory` rows but happen inside one handler
invocation, because Job Matching Service always emits `jobs.shortlisted`
and `contacts.requested` together (see
[kafka-topics.md](kafka-topics.md)) — by the time Tracking Service
processes `jobs.shortlisted`, contact discovery has already been requested,
so there is no meaningful window where the opportunity is "shortlisted but
contact search has not been requested."

## Job processing lifecycle (`Job.processing_status` / `JobProcessingStatus`)

Not owned by a single component the way every other lifecycle in this
document is — `jobs` is the one documented shared-write table (see
[database-ownership.md#jobs](database-ownership.md#jobs) and
[ownership.md#shared-write-jobs-table](ownership.md#shared-write-jobs-table)).
Instead, exactly one component owns each transition:

```
(row does not exist yet)
    │  INSERT, source_type=MANUAL_URL — Job Ingestion Service,
    │  only after synchronous page-fetch + extraction succeeds
    │
    │  INSERT, source_type=<automatic> — Job Discovery Service,
    │  once per successfully extracted posting
    ▼
NORMALIZED
    │  UPDATE — Job Matching Service only, after it finishes matching
    ├──────────────────────────┬──────────────────────────┐
    ▼                          ▼                          ▼
MATCHED [terminal]      FAILED [terminal]           FAILED [terminal]
  a JobMatch row        NO_PROFILES_AVAILABLE:       any other matching
  was persisted;         zero ACTIVE profiles for     failure (LLM/DB/
  jobs.matched (+        the user — no JobMatch       publish) after at
  jobs.shortlisted       row, no jobs.matched          least one profile
  if applicable) was     event; see                   was evaluated — see
  published              langgraph-state.md's          component-contracts
                          persist_and_publish entry     .md's MATCHING_FAILED
```

**Ownership rule, made explicit:** Job Ingestion Service and Job Discovery
Service each set `processing_status`'s *initial* value at `INSERT` time
only — this is covered by their "May create" grant, not an `UPDATE`, and
neither ever writes to a `jobs` row it did not create. Job Matching Service
holds the sole `UPDATE` grant on this column (`database-ownership.md#jobs`)
and is the only component that ever transitions a row away from
`NORMALIZED`. No row is ever inserted already at `MATCHED` or `FAILED` by
an ingestion path.

**Terminal states:** `MATCHED`, `FAILED`. Both are reachable only via Job
Matching Service's single `UPDATE` transition above — `FAILED` is not
reachable from an ingestion/discovery path (a manual URL that fails
extraction never gets a `Job` row at all; see
`component-contracts.md#job-ingestion-service`).

**`PENDING`:** defined in the shared `JobProcessingStatus` enum
(`shared-types.md#shared-enums`) but not set by any current component.
Job Ingestion Service's `POST /jobs/ingest-url` is synchronous-only —
extraction always completes within the request before a row is written, so
there is no persisted intermediate state between "not yet ingested" and
`NORMALIZED`. `PENDING` is reserved for a possible future asynchronous
ingestion variant; introducing one would require its own documented
`UPDATE` grant in `database-ownership.md`, not reuse of Job Matching
Service's existing one.

## Resume lifecycle (`Resume.status` / `ResumeStatus`)

Owned entirely by **Resume/Profile Service**.

```
UPLOADED
    │  upload handler enqueues parsing
    ▼
PARSING
    │  parse succeeds        │  parse fails
    ▼                        ▼
PARSED                  PARSE_FAILED [terminal]
    │  DELETE /resumes/{id}
    ▼
ARCHIVED [terminal]
```

`PARSE_FAILED` is terminal for that `Resume` row — retry is a new upload
(new `Resume` row), not a re-attempt on the failed one, keeping the audit
trail honest about what was actually tried.

## CandidateProfile lifecycle (`CandidateProfile.status` / `ProfileStatus`)

Owned entirely by **Resume/Profile Service**.

```
ACTIVE ──DELETE /resumes/{source resume id}──► ARCHIVED [terminal]
```

Only two states. A profile becomes `ACTIVE` at creation (parsing success)
and only ever moves to `ARCHIVED` when its source resume is removed.

## Contact lifecycle (`Contact.status` / `ContactStatus`)

Owned entirely by **Contact Discovery Service**.

```
DISCOVERED
    │  ranking completes (same LangGraph run)
    ▼
RANKED
    │  manual archive (job withdrawn, or user request)
    ▼
ARCHIVED [terminal]
```

`ContactStatus` deliberately has no `CONTACTED` value and Contact Discovery
Service does not consume `outreach.sent`. Whether a contact has actually
been reached out to is answered by querying `Outreach` records for that
`contact_id` (via Outreach Service's read API), not by a status flag on
`Contact` — storing it redundantly would require Contact Discovery Service
to consume an Outreach Service event while Outreach Service already
consumes a Contact Discovery Service event (`contacts.found`), which would
create a circular event dependency between the two components. See
[kafka-topics.md](kafka-topics.md) and
[dependency-graph.md](dependency-graph.md#no-circular-dependencies).

## Outreach lifecycle (`Outreach.status` / `OutreachStatus`)

Owned entirely by **Outreach Service**. `DRAFT` is a transient in-memory
`OutreachGenerationState` value during LangGraph execution and is never
written to the `outreach` table — the row is created directly at
`PENDING_APPROVAL`.

```
(generation workflow completes)
    │
    ▼
PENDING_APPROVAL
    │  POST /outreach/{id}/edit          │  POST /outreach/{id}/approve   │  POST /outreach/{id}/reject
    ▼                                    ▼                                 ▼
EDITED                               APPROVED                          REJECTED [terminal]
    │  POST /outreach/{id}/approve       │  outreach.approved consumed
    │  POST /outreach/{id}/reject        │  (send worker)
    ▼                                    ▼
APPROVED or REJECTED [terminal]     SENT [terminal]  or  SEND_FAILED [terminal]
```

This is the state machine that enforces CLAUDE.md's "external outreach
requires human approval" rule structurally: the send worker only consumes
`outreach.approved`, and that event is only ever published from the
`/approve` handler, which only runs on a row still in `PENDING_APPROVAL` or
`EDITED`. There is no code path that reaches `SENT` without passing through
`APPROVED`.

`SEND_FAILED` is terminal — an external provider failure after retries
requires operator/user intervention (e.g. re-approve to retry, out of
current scope), not an automatic infinite retry loop, matching
about_project.md's non-goal of uncontrolled automated outreach.

## Agent execution lifecycle (`WorkflowExecution.status` / `WorkflowStatus`)

Shared shape, written by whichever component owns the run (see
[database-ownership.md#shared-observability-table](database-ownership.md#shared-observability-table)).

```
PENDING
    │  LangGraph run starts
    ▼
RUNNING ──error, retries remain──► RETRYING ──┐
    │                                          │
    │  success                    (loops back to RUNNING)
    ▼
COMPLETED [terminal]

RUNNING ──error, retries exhausted──► FAILED [terminal]
```

Node/tool-call granularity within a single `RUNNING` execution uses
`AgentExecutionStatus` (not persisted per-node, used only for structured
logging/tracing within a run):

```
STARTED ──► SUCCEEDED
STARTED ──► FAILED ──► RETRIED ──► STARTED   (loops until node-level retry budget exhausted)
STARTED ──► SKIPPED   (conditional routing bypassed this node)
```
