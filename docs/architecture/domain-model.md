# Domain Model

Canonical entities shared across the system. Every field listed here is the
**only** representation of that concept — components do not define their own
`Job` or `Contact` shape.

Identifier types (`UserId`, `JobId`, etc.) are defined in
[shared-types.md#identifiers](shared-types.md#identifiers) — all are UUIDv4
strings, wrapped as distinct types for type safety. All timestamps are UTC
`datetime`.

For each entity: purpose, fields, identifiers, relationships, ownership,
persistence, Kafka appearance, and which components may modify it (write
access — everyone else is read-only via API/event).

---

## User

**Purpose:** The account holder. Represents one person using the platform,
independent of profession.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `UserId` | yes | primary identifier |
| `email` | `str` | yes | unique |
| `display_name` | `str` | yes | |
| `created_at` | `datetime` | yes | |
| `timezone` | `str` | no | IANA tz name, used for follow-up scheduling |

**Relationships:** parent of `Resume`, `CandidateProfile`, `UserPreferences`,
`Job`, `Application` (all scoped by `user_id`).

**Ownership:** User Service.
**Persisted:** yes — `users` table.
**Appears in Kafka events:** no — `User` itself is never a payload. Only
`user_id` (a `UserId`) is carried inside other event payloads for scoping.
**Modifiable by:** User Service only.

---

## UserPreferences

**Purpose:** Search behavior configuration used by Job Discovery and as a
matching input signal (location/comp/remote fit).

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `ProfileId`-like UUID (`UserPreferencesId`) | yes | |
| `user_id` | `UserId` | yes | one active row per user |
| `target_roles` | `list[str]` | no | free-text role titles the user is targeting |
| `target_locations` | `list[str]` | no | |
| `remote_preference` | `RemoteWorkPreference` (enum) | no | `REMOTE`, `HYBRID`, `ONSITE`, `NO_PREFERENCE` |
| `excluded_companies` | `list[str]` | no | |
| `min_salary` | `int` | no | in user's stated currency |
| `salary_currency` | `str` | no | ISO 4217 code |
| `updated_at` | `datetime` | yes | |

**Relationships:** belongs to one `User`.
**Ownership:** User Service.
**Persisted:** yes — `user_preferences` table.
**Appears in Kafka events:** no — read by Job Discovery Service and Job
Matching Service via API/shared read, not published. A full copy is embedded
by value inside the `JobMatchingState` LangGraph state at workflow start
(see [langgraph-state.md](langgraph-state.md)).
**Modifiable by:** User Service only.

---

## Resume

**Purpose:** An uploaded resume file plus its raw extracted text. Distinct
from `CandidateProfile`, which is the *understood* profile derived from it.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `ResumeId` | yes | |
| `user_id` | `UserId` | yes | |
| `file_name` | `str` | yes | |
| `storage_uri` | `str` | yes | location of the original file |
| `raw_text` | `str` | no | extracted text; absent until parsed |
| `status` | `ResumeStatus` (enum) | yes | see [shared-enums](shared-types.md#shared-enums) |
| `uploaded_at` | `datetime` | yes | |
| `parsed_at` | `datetime` | no | set when parsing completes |
| `parse_error` | `str` | no | set only if `status == PARSE_FAILED` |

**Relationships:** belongs to one `User`; source of exactly one
`CandidateProfile` (1:1 — a new resume upload always produces a new
profile; replacing means uploading a new resume and archiving the old
profile, not mutating one in place).

**Ownership:** Resume/Profile Service.
**Persisted:** yes — `resumes` table.
**Appears in Kafka events:** no. Resume content (large text/binary) never
travels on Kafka; only derived, small `ProfileUpdatedEvent` payloads do (see
below).
**Modifiable by:** Resume/Profile Service only.

---

## CandidateProfile

**Purpose:** The system's *understanding* of one professional identity
derived from a resume — skills, experience, target roles. A user with
multiple resumes has multiple profiles (AI Engineer, Java Backend, etc.).
This is the entity that keeps the platform profession-independent: it has no
field that assumes any specific domain.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `ProfileId` | yes | |
| `user_id` | `UserId` | yes | |
| `resume_id` | `ResumeId` | yes | source resume |
| `title` | `str` | yes | inferred label, e.g. "Java Backend Engineer" |
| `summary` | `str` | no | short inferred professional summary |
| `skills` | `list[str]` | yes | may be empty list, never null |
| `experience_years` | `float` | no | total relevant experience |
| `seniority` | `str` | no | free-text inferred level (e.g. "Senior", "Entry") — deliberately not an enum, since seniority vocabulary varies by profession |
| `education` | `list[EducationEntry]` | no | see [shared-types.md](shared-types.md) |
| `certifications` | `list[str]` | no | |
| `projects` | `list[str]` | no | short descriptions |
| `industries` | `list[str]` | no | inferred industry fit |
| `target_roles` | `list[str]` | no | roles this profile is suited for |
| `status` | `ProfileStatus` (enum) | yes | `ACTIVE`, `ARCHIVED` |
| `generated_at` | `datetime` | yes | |

**Relationships:** belongs to one `User`; derived from exactly one `Resume`;
referenced by `JobMatch.selected_profile_id`.

**Ownership:** Resume/Profile Service (Profile Intelligence).
**Persisted:** yes — `candidate_profiles` table.
**Appears in Kafka events:** indirectly — `ProfileUpdatedEvent` carries
`profile_id`, `user_id`, `resume_id`, and a `change_type`, but not the full
profile body (consumers that need the full profile fetch it via API or
already hold a cached `ResumeProfile` — see
[shared-types.md#resumeprofile](shared-types.md#resumeprofile)).
**Modifiable by:** Resume/Profile Service only.

---

## Job

**Purpose:** A single opportunity as ingested (manually via URL or
automatically via discovery), before any matching decision.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `JobId` | yes | |
| `user_id` | `UserId` | yes | the user this job was discovered/ingested for |
| `source_type` | `JobSourceType` (enum) | yes | `MANUAL_URL` or an automatic source |
| `source_id` | `JobSourceId` | no | set when `source_type` is an automatic source; references `JobSource` |
| `source_url` | `str` | no | original URL; required if `source_type == MANUAL_URL` |
| `company` | `str` | yes | |
| `title` | `str` | yes | |
| `location` | `str` | no | |
| `description_raw` | `str` | yes | full extracted job text |
| `extracted_skills` | `list[str]` | no | |
| `experience_required` | `str` | no | free text, e.g. "3-5 years" |
| `processing_status` | `JobProcessingStatus` (enum) | yes | ingestion-internal status, see [shared-enums](shared-types.md#shared-enums) |
| `discovered_at` | `datetime` | yes | |

**Relationships:** belongs to one `User`; optionally belongs to one
`JobSource`; has zero-or-one `JobMatch`; is the anchor for exactly one
`Application` (created by Tracking Service once the job is discovered).

**Ownership:** Job Ingestion Service (manual path) / Job Discovery Service
(automatic path) — both write to the same `jobs` table using the same
schema; see [ownership.md](ownership.md) for the shared-writer justification.
**Persisted:** yes — `jobs` table.
**Appears in Kafka events:** yes, in normalized form — see `NormalizedJob`
in [shared-types.md](shared-types.md), the payload of `JobDiscoveredEvent`.
**Modifiable by:** Job Ingestion Service, Job Discovery Service (creation
only — see [ownership.md](ownership.md#shared-write-jobs-table) for the
narrow update rights each retains), Job Matching Service (may update
`processing_status` only, after it finishes matching).

---

## JobSource

**Purpose:** Configuration for an automatic discovery source (a job board,
search query, or company careers feed).

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `JobSourceId` | yes | |
| `user_id` | `UserId` | yes | sources are configured per user |
| `name` | `str` | yes | e.g. "LinkedIn — Backend roles" |
| `type` | `JobSourceType` (enum) | yes | |
| `query_config` | `dict[str, Any]` | no | source-specific search parameters (JSON) |
| `enabled` | `bool` | yes | |
| `last_run_at` | `datetime` | no | |

**Relationships:** belongs to one `User`; parent of zero-or-more `Job`.
**Ownership:** Job Discovery Service.
**Persisted:** yes — `job_sources` table.
**Appears in Kafka events:** no.
**Modifiable by:** Job Discovery Service only.

---

## JobMatch

**Purpose:** The analytical record of comparing one `Job` against a user's
`CandidateProfile`s and selecting the best one. This is the Job Matching
Service's own record of *how* it reached a decision — distinct from
`Application`, which is the user-facing lifecycle record (owned by
Tracking). A `JobMatch` row only ever exists for a job that had at least
one candidate profile to evaluate — `selected_profile_id`/
`selected_resume_id` stay non-nullable rather than being widened to
represent "no profile was available"; that outcome is represented by the
absence of a row instead (see
[state-machines.md#job-processing-lifecycle](state-machines.md#job-processing-lifecycle)
and
[langgraph-state.md#jobmatchingstate](langgraph-state.md#jobmatchingstate)'s
`persist_and_publish` entry for the ratified `NO_PROFILES_AVAILABLE`
terminal shape).

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `JobMatchId` | yes | |
| `job_id` | `JobId` | yes | |
| `user_id` | `UserId` | yes | |
| `selected_profile_id` | `ProfileId` | yes | |
| `selected_resume_id` | `ResumeId` | yes | |
| `match_score` | `float` | yes | 0.0–1.0 |
| `matched_skills` | `list[str]` | yes | may be empty |
| `missing_skills` | `list[str]` | yes | may be empty |
| `recommendation` | `MatchRecommendation` (enum) | yes | `SHORTLIST`, `BORDERLINE`, `IGNORE` |
| `profile_scores` | `list[ProfileMatchScore]` | no | score against every evaluated profile, not just the winner — see [shared-types.md](shared-types.md) |
| `matched_at` | `datetime` | yes | |

**Relationships:** belongs to one `Job`; references one `CandidateProfile`
and one `Resume`.
**Ownership:** Job Matching Service.
**Persisted:** yes — `job_matches` table.
**Appears in Kafka events:** yes — payload of `JobMatchedEvent` and
`JobShortlistedEvent` (as `JobMatchResult`, see
[shared-types.md](shared-types.md)).
**Modifiable by:** Job Matching Service only. Immutable once written in
practice (re-matching after a `ProfileUpdatedEvent` creates a new
`JobMatch` row rather than mutating the old one, preserving history).

---

## Contact

**Purpose:** A person discovered at the target company who may be useful for
referral or networking outreach.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `ContactId` | yes | |
| `job_id` | `JobId` | yes | the opportunity this contact was found for |
| `user_id` | `UserId` | yes | |
| `full_name` | `str` | yes | |
| `headline` | `str` | no | e.g. "Senior Mechanical Engineer at Acme" |
| `company` | `str` | yes | |
| `contact_type` | `ContactType` (enum) | yes | generic across professions — see [shared-enums](shared-types.md#shared-enums) |
| `profile_url` | `str` | no | e.g. LinkedIn profile URL |
| `email` | `str` | no | if discoverable |
| `status` | `ContactStatus` (enum) | yes | |
| `discovered_at` | `datetime` | yes | |

**Relationships:** belongs to one `Job` (and transitively one `User`); has
zero-or-one `ContactScore`; referenced by zero-or-more `Outreach` records.
**Ownership:** Contact Discovery Service.
**Persisted:** yes — `contacts` table.
**Appears in Kafka events:** yes — as part of `ContactRankingResult`, the
payload of `ContactsFoundEvent`.
**Modifiable by:** Contact Discovery Service only.

---

## ContactScore

**Purpose:** The ranking signal breakdown and final relevance score for one
contact, relative to one job.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `ContactScoreId` | yes | |
| `contact_id` | `ContactId` | yes | |
| `job_id` | `JobId` | yes | |
| `relevance_score` | `float` | yes | 0.0–10.0 |
| `same_company` | `bool` | yes | |
| `department_relevance` | `float` | no | 0.0–1.0 |
| `role_similarity` | `float` | no | 0.0–1.0 |
| `seniority_fit` | `float` | no | 0.0–1.0 |
| `ranked_at` | `datetime` | yes | |

**Relationships:** belongs to one `Contact`.
**Ownership:** Contact Discovery Service (ranking is a sub-responsibility of
this service — see [service-boundaries.md](service-boundaries.md)).
**Persisted:** yes — `contact_rankings` table.
**Appears in Kafka events:** yes — embedded in `ContactRankingResult` items
inside `ContactsFoundEvent`.
**Modifiable by:** Contact Discovery Service only.

---

## Outreach

**Purpose:** A single piece of generated professional communication, its
approval state, and its send state. This is where the human-in-the-loop gate
lives.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `OutreachId` | yes | |
| `job_id` | `JobId` | yes | |
| `contact_id` | `ContactId` | yes | |
| `user_id` | `UserId` | yes | |
| `channel` | `OutreachChannel` (enum) | yes | |
| `draft_message` | `str` | yes | generated message |
| `final_message` | `str` | no | set if the user edits before approving |
| `status` | `OutreachStatus` (enum) | yes | see [state-machines.md](state-machines.md#outreach-lifecycle) |
| `generated_at` | `datetime` | yes | |
| `decided_at` | `datetime` | no | when approved/rejected |
| `decided_by` | `UserId` | no | |
| `sent_at` | `datetime` | no | |
| `external_message_id` | `str` | no | provider-assigned id after send |
| `send_error` | `str` | no | set only if `status == SEND_FAILED` |

**Relationships:** belongs to one `Job`, one `Contact`, one `User`.
**Ownership:** Outreach Service.
**Persisted:** yes — `outreach` table.
**Appears in Kafka events:** yes — `OutreachGeneratedEvent`,
`OutreachApprovedEvent`, `OutreachSentEvent` each carry a payload derived
from this entity (see [event-contracts.md](event-contracts.md)).
**Modifiable by:** Outreach Service only. The human approval action is
always mediated through the Outreach Service's API — no other component,
including Tracking, ever writes to this table.

---

## Application

**Purpose:** The single, canonical, user-facing lifecycle record for one
opportunity — from discovery through offer/rejection. This is "the tracker"
referenced throughout about_project.md. Its `status` field **is** the
opportunity lifecycle state machine (see
[state-machines.md](state-machines.md#opportunity-lifecycle)).

`Application` is intentionally the aggregation point: it is built entirely by
consuming events produced by every other component, never by those
components writing to it directly. This is what lets Contact Discovery,
Matching, and Outreach evolve independently without coordinating on a shared
table.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `ApplicationId` | yes | |
| `job_id` | `JobId` | yes | 1:1 with `Job` |
| `user_id` | `UserId` | yes | |
| `company` | `str` | yes | denormalized from `Job` for fast listing |
| `title` | `str` | yes | denormalized from `Job` |
| `status` | `ApplicationStatus` (enum) | yes | see [state-machines.md](state-machines.md#opportunity-lifecycle) |
| `selected_resume_id` | `ResumeId` | no | populated once matched |
| `match_score` | `float` | no | populated once matched |
| `matched_skills` | `list[str]` | no | |
| `missing_skills` | `list[str]` | no | |
| `referral_contact_id` | `ContactId` | no | populated if an outreach path was used |
| `applied_date` | `date` | no | set when status reaches `APPLIED` |
| `follow_up_date` | `date` | no | user- or system-suggested |
| `notes` | `str` | no | free-text user notes |
| `created_at` | `datetime` | yes | |
| `updated_at` | `datetime` | yes | |

**Relationships:** belongs to one `User`; 1:1 with one `Job`; has
many `ApplicationHistory` entries.
**Ownership:** Tracking Service.
**Persisted:** yes — `applications` table.
**Appears in Kafka events:** yes — `ApplicationUpdatedEvent` payload
(`ApplicationStatusUpdate`).
**Modifiable by:** Tracking Service only — including for status changes
that originate from a user action (e.g. manually marking `APPLIED`), which
go through the Tracking Service's own API, not through the originating
component.

---

## ApplicationHistory

**Purpose:** Append-only audit trail of every status transition an
`Application` has gone through.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `ApplicationHistoryId` | yes | |
| `application_id` | `ApplicationId` | yes | |
| `from_status` | `ApplicationStatus` (enum) | no | null for the first entry |
| `to_status` | `ApplicationStatus` (enum) | yes | |
| `changed_at` | `datetime` | yes | |
| `triggered_by` | `str` | yes | source component or `"user"` |
| `source_event_type` | `EventType` (enum) | no | null if triggered by a direct API call |
| `correlation_id` | `CorrelationId` | no | |

**Relationships:** belongs to one `Application`.
**Ownership:** Tracking Service.
**Persisted:** yes — `application_history` table. Append-only; rows are
never updated or deleted.
**Appears in Kafka events:** no — this is a derived audit record, not a
source of new events.
**Modifiable by:** Tracking Service only (insert-only).

---

## WorkflowExecution (the "WorkflowState" entity)

**Purpose:** An observability/audit record of one LangGraph workflow run
(one job-matching run, one contact-discovery run, one outreach-generation
run). This is distinct from the **typed in-memory LangGraph state** used
while a workflow executes (`JobMatchingState`, etc. — see
[langgraph-state.md](langgraph-state.md)); `WorkflowExecution` is the durable
record written *about* that run, primarily start/end/error, not a live copy
of every intermediate field.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | `WorkflowExecutionId` | yes | |
| `workflow_type` | `WorkflowType` (enum) | yes | `JOB_MATCHING`, `CONTACT_DISCOVERY`, `OUTREACH_GENERATION` |
| `correlation_id` | `CorrelationId` | yes | ties this run back to the originating event chain |
| `entity_ref_id` | `str` | yes | the `JobId`/`ContactId`/`OutreachId` this run operated on |
| `status` | `WorkflowStatus` (enum) | yes | see [shared-enums](shared-types.md#shared-enums) |
| `current_node` | `str` | no | last node entered |
| `started_at` | `datetime` | yes | |
| `completed_at` | `datetime` | no | |
| `error` | `str` | no | |
| `retry_count` | `int` | yes | default 0 |

**Relationships:** references a `Job`, `Contact`, or `Outreach` by
`entity_ref_id` depending on `workflow_type` (polymorphic reference, not a
foreign key — kept loose deliberately since this is observability data, not
business state).

**Ownership:** each component that runs a LangGraph workflow (Job Matching
Service, Contact Discovery Service, Outreach Service) writes only the rows
it creates. See
[database-ownership.md#shared-observability-table](database-ownership.md#shared-observability-table)
for the row-level ownership rule that makes this a documented exception to
"one table, one writer."

**Persisted:** yes — shared `workflow_executions` table (row-level
ownership by `workflow_type`).
**Appears in Kafka events:** no. Purely internal observability; never
published.
**Modifiable by:** the component whose `workflow_type` a row belongs to,
and only for rows it created.

---

## Entity relationship summary

```
User ──1:N── Resume ──1:1── CandidateProfile
  │
  ├──1:1── UserPreferences
  │
  ├──1:N── JobSource
  │
  └──1:N── Job ──1:1── Application ──1:N── ApplicationHistory
              │            │
              │            └── references ContactId, ResumeId (denormalized)
              │
              ├──0:1── JobMatch ──refs──> CandidateProfile, Resume
              │
              ├──1:N── Contact ──0:1── ContactScore
              │            │
              │            └──1:N── Outreach
              │
              └──0:N── WorkflowExecution (polymorphic ref)
```

Note: `Application` is 1:1 with `Job`, but is *owned and written* by a
different component (Tracking) than `Job` (Job Ingestion/Discovery). This is
intentional — see [ownership.md](ownership.md).
