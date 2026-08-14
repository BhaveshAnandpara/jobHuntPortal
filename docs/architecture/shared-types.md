# Shared Types and Schemas

Canonical types used for communication between components. **No component
may define its own version of any type listed here.** If a type you need
isn't here, it's an architecture gap — raise it, don't invent a local
equivalent.

All types are Pydantic `BaseModel` subclasses unless stated otherwise
(LangGraph state types are the one exception — see
[langgraph-state.md](langgraph-state.md)). They live under `shared/types/` in
the repository (see [repository-structure.md](repository-structure.md)).

## Identifiers

All IDs are UUIDv4, represented as distinct types (via `typing.NewType` over
`UUID`) so a `JobId` can never be passed where a `ContactId` is expected
without an explicit cast. Defined in `shared/types/ids.py`.

| Type | Wraps | Identifies |
|---|---|---|
| `UserId` | `UUID` | `User` |
| `ResumeId` | `UUID` | `Resume` |
| `ProfileId` | `UUID` | `CandidateProfile` |
| `UserPreferencesId` | `UUID` | `UserPreferences` |
| `JobId` | `UUID` | `Job` |
| `JobSourceId` | `UUID` | `JobSource` |
| `JobMatchId` | `UUID` | `JobMatch` |
| `ContactId` | `UUID` | `Contact` |
| `ContactScoreId` | `UUID` | `ContactScore` |
| `OutreachId` | `UUID` | `Outreach` |
| `ApplicationId` | `UUID` | `Application` |
| `ApplicationHistoryId` | `UUID` | `ApplicationHistory` |
| `WorkflowExecutionId` | `UUID` | `WorkflowExecution` |
| `EventId` | `UUID` | one Kafka event instance |
| `CorrelationId` | `UUID` | one causal chain across many events/workflows |

`CorrelationId` is generated once at the origin of a chain — when a `Job` is
discovered — and propagated unchanged through every downstream event
(`jobs.discovered` → `jobs.matched` → `jobs.shortlisted` →
`contacts.requested` → ... → `applications.updated`). It is how a log line,
trace, or the `application_history` table can reconstruct the full causal
path for one opportunity.

## Cross-cutting DTOs

Used inside multiple entity/event definitions.

```
EducationEntry
    institution: str
    degree: str | None
    field_of_study: str | None
    graduation_year: int | None

ProfileMatchScore
    profile_id: ProfileId
    resume_id: ResumeId
    score: float                  # 0.0-1.0
    matched_skills: list[str]
    missing_skills: list[str]

WorkflowError
    node: str
    error_code: ErrorCode         # see shared enums / error codes below
    message: str
    occurred_at: datetime
```

## Canonical exchange types

These are the types the task brief calls out by name. Each is the *only*
shape used for its concept anywhere it crosses a component boundary
(API, event, or LangGraph state).

### ResumeProfile

The read/composition type combining a `Resume` and its derived
`CandidateProfile`, used whenever another component needs "a user's
profile" — most notably as an input to Job Matching. It is **not** persisted
directly; it is assembled on read from `resumes` + `candidate_profiles`.

```
ResumeProfile
    profile_id: ProfileId
    resume_id: ResumeId
    user_id: UserId
    title: str
    summary: str | None
    skills: list[str]
    experience_years: float | None
    seniority: str | None
    education: list[EducationEntry]
    certifications: list[str]
    projects: list[str]
    industries: list[str]
    target_roles: list[str]
    status: ProfileStatus
```

**Produced by:** Resume/Profile Service (exposed via `GET /profiles`,
`GET /profiles/{id}`).
**Consumed by:** Job Matching Service (as the `profiles` field of
`JobMatchingState`).

### NormalizedJob

The canonical job representation used everywhere *after* ingestion — this is
what travels on Kafka and what Matching/Contact Discovery/Outreach operate
on, instead of the raw `Job` DB record.

```
NormalizedJob
    job_id: JobId
    user_id: UserId
    company: str
    title: str
    location: str | None
    description: str
    extracted_skills: list[str]
    experience_required: str | None
    source_type: JobSourceType
    source_url: str | None
    discovered_at: datetime
```

**Produced by:** Job Ingestion Service (manual URL path) and Job Discovery
Service (automatic path) — both emit this exact shape.
**Consumed by:** Job Matching Service, and embedded by value in
`JobMatchingState`.
**Transformation:** `Job` (DB record) → `NormalizedJob` happens once, at
publish time, inside whichever service created the `Job` row. It is a pure
projection — no re-fetch needed downstream.

### JobMatchResult

The output of Job Matching — the wire/event form of `JobMatch`.

```
JobMatchResult
    job_match_id: JobMatchId
    job_id: JobId
    user_id: UserId
    selected_profile_id: ProfileId
    selected_resume_id: ResumeId
    match_score: float
    matched_skills: list[str]
    missing_skills: list[str]
    recommendation: MatchRecommendation
    matched_at: datetime
```

**Produced by:** Job Matching Service.
**Consumed by:** Tracking Service (always); this exact type is the payload
of both `JobMatchedEvent` and `JobShortlistedEvent` — see
[event-contracts.md](event-contracts.md) for why one type serves two events.
**Transformation:** `JobMatch` (DB record, includes `profile_scores` for
every candidate profile evaluated) → `JobMatchResult` (event, winner only)
drops the `profile_scores` list — downstream components don't need
runner-up scores, only the DB record retains them for audit/debugging.

### ContactCandidate

A single discovered contact, before ranking is finalized. Used inside the
Contact Discovery LangGraph state between the search and rank nodes.

```
ContactCandidate
    full_name: str
    headline: str | None
    company: str
    contact_type: ContactType
    profile_url: str | None
    email: str | None
    source: str                  # which tool/API found this candidate
```

**Produced by:** Contact Discovery Service's `search_contacts` node.
**Consumed by:** the same workflow's `rank_contacts` node. Never crosses a
component boundary — it is upgraded to `Contact` + `ContactScore` (persisted)
before being published.

### ContactRankingResult

The output of contact discovery+ranking — the wire/event form covering both
`Contact` and `ContactScore` for every ranked contact found for a job.

```
RankedContact
    contact_id: ContactId
    full_name: str
    headline: str | None
    contact_type: ContactType
    profile_url: str | None
    relevance_score: float        # 0.0-10.0

ContactRankingResult
    job_id: JobId
    user_id: UserId
    contacts: list[RankedContact]  # ordered, highest relevance_score first
    ranked_at: datetime
```

**Produced by:** Contact Discovery Service.
**Consumed by:** Outreach Service, Tracking Service — payload of
`ContactsFoundEvent`.

### OutreachDraft

The generated-but-unapproved message — the wire/event form of `Outreach` at
creation time.

```
OutreachDraft
    outreach_id: OutreachId
    job_id: JobId
    contact_id: ContactId
    user_id: UserId
    channel: OutreachChannel
    draft_message: str
    generated_at: datetime
```

**Produced by:** Outreach Service.
**Consumed by:** Tracking Service — payload of `OutreachGeneratedEvent`.
Also read directly (via API, not Kafka) by whatever UI surfaces the human
approval step.

### ApplicationStatusUpdate

The single event payload type for any `Application.status` change,
regardless of which upstream event or user action caused it.

```
ApplicationStatusUpdate
    application_id: ApplicationId
    job_id: JobId
    user_id: UserId
    previous_status: ApplicationStatus | None   # null for the first status
    new_status: ApplicationStatus
    changed_at: datetime
    triggered_by: str                            # component name or "user"
```

**Produced by:** Tracking Service only.
**Consumed by:** reserved for future Analytics; currently optional.

## Type layers, explicitly

| Layer | Examples | Suffix convention |
|---|---|---|
| Internal domain types | `CandidateProfile`, `Job`, `Contact`, `Outreach`, `Application` (as defined in [domain-model.md](domain-model.md)) | bare noun |
| API request/response types | `CreateResumeRequest`, `ResumeResponse` | `*Request` / `*Response` |
| Kafka event payload types | `JobDiscoveredEvent`, `ContactsFoundEvent` | `*Event` |
| Database models | `ResumeRecord`, `JobMatchRecord` | `*Record` |
| LangGraph workflow state types | `JobMatchingState` | `*State` |

## Transformation rules

Transformations are always one-directional and always named. A component
never mutates a type in place across a layer boundary — it constructs a new
instance of the target type.

| From | To | Where it happens | Notes |
|---|---|---|---|
| `CreateResumeRequest` | `Resume` (domain) → `ResumeRecord` (DB) | Resume/Profile Service API handler | |
| `ResumeRecord` + parsed fields | `CandidateProfile` (domain) → `CandidateProfileRecord` (DB) | Resume/Profile Service parsing workflow | |
| `CandidateProfileRecord` + `ResumeRecord` | `ResumeProfile` | Resume/Profile Service read path | assembled on every read, not stored |
| `IngestJobUrlRequest` or discovery result | `Job` (domain) → `JobRecord` (DB) | Job Ingestion / Job Discovery Service | |
| `JobRecord` | `NormalizedJob` | same service, at publish time | dropped: internal `processing_status` |
| `NormalizedJob` + `ResumeProfile` list + `UserPreferences` | `JobMatchingState` (LangGraph) | Job Matching Service, workflow entry | |
| `JobMatchingState.final_match` | `JobMatch` (domain) → `JobMatchRecord` (DB) | Job Matching Service, workflow exit | |
| `JobMatchRecord` | `JobMatchResult` | Job Matching Service, at publish time | drops `profile_scores` |
| `JobMatchResult` (from `jobs.shortlisted`) | `ContactDiscoveryState` (LangGraph) | Contact Discovery Service, workflow entry | |
| `ContactDiscoveryState` candidates | `ContactCandidate` → `Contact`/`ContactScore` (DB) | Contact Discovery Service, `rank_contacts` node | |
| `Contact`/`ContactScore` records | `ContactRankingResult` | Contact Discovery Service, at publish time | |
| `ContactRankingResult` | `OutreachGenerationState` (LangGraph) | Outreach Service, workflow entry | |
| `OutreachGenerationState.draft_message` | `Outreach` (domain) → `OutreachRecord` (DB) | Outreach Service, workflow exit | |
| `OutreachRecord` | `OutreachDraft` | Outreach Service, at publish time | |
| any event payload | `ApplicationStatusUpdate` → `ApplicationRecord` update + `ApplicationHistoryRecord` insert | Tracking Service, on every consumed event | |
| `ApplicationRecord` | `ApplicationResponse` | Tracking Service API read path | |

## Shared enums

Full definitions with every member live in `shared/types/enums.py`. Listed
here so no component invents its own strings for the same concept — this is
the direct fix for the "shortlisted" vs "SHORT_LISTED" vs "selected"
problem named in the brief.

| Enum | Values | Owner (who may add values) |
|---|---|---|
| `ResumeStatus` | `UPLOADED`, `PARSING`, `PARSED`, `PARSE_FAILED`, `ARCHIVED` | Resume/Profile Service |
| `ProfileStatus` | `ACTIVE`, `ARCHIVED` | Resume/Profile Service |
| `JobSourceType` | `MANUAL_URL`, `LINKEDIN`, `INDEED`, `COMPANY_CAREERS_PAGE`, `OTHER` | Job Discovery Service (extensible — adding a source is additive) |
| `JobProcessingStatus` | `PENDING`, `NORMALIZED`, `MATCHED`, `FAILED` | Job Ingestion / Job Discovery Service |
| `MatchRecommendation` | `SHORTLIST`, `BORDERLINE`, `IGNORE` | Job Matching Service |
| `ContactType` | `PRACTITIONER`, `TEAM_LEAD`, `HIRING_MANAGER`, `RECRUITER`, `EXECUTIVE`, `DEPARTMENT_LEADER`, `OTHER` | Contact Discovery Service — deliberately profession-generic (see below) |
| `ContactStatus` | `DISCOVERED`, `RANKED`, `ARCHIVED` | Contact Discovery Service |
| `OutreachChannel` | `EMAIL`, `LINKEDIN_MESSAGE`, `LINKEDIN_CONNECTION_REQUEST`, `OTHER` | Outreach Service |
| `OutreachStatus` | `DRAFT`, `PENDING_APPROVAL`, `APPROVED`, `REJECTED`, `EDITED`, `SENT`, `SEND_FAILED` | Outreach Service |
| `ApplicationStatus` | see [state-machines.md](state-machines.md#opportunity-lifecycle) | Tracking Service |
| `RemoteWorkPreference` | `REMOTE`, `HYBRID`, `ONSITE`, `NO_PREFERENCE` | User Service |
| `EventType` | one member per event payload type, see [event-contracts.md](event-contracts.md) | shared — additive only |
| `WorkflowType` | `JOB_MATCHING`, `CONTACT_DISCOVERY`, `OUTREACH_GENERATION` | shared — additive only |
| `WorkflowStatus` | `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `RETRYING` | shared |
| `AgentExecutionStatus` | `STARTED`, `SUCCEEDED`, `FAILED`, `SKIPPED`, `RETRIED` | shared — node/tool-call granularity, distinct from `WorkflowStatus` (run granularity) |

`ContactType` is intentionally generic instead of using
about_project.md's software-engineering examples ("Software Engineers",
"Engineering Managers") or HR examples ("HR Business Partners") verbatim.
Each profession's specific title (e.g. "Senior Mechanical Engineer") lives in
`Contact.headline` as free text; `contact_type` only captures the
*functional tier* (practitioner vs. lead vs. hiring manager vs. recruiter vs.
executive) that Contact Ranking scores against, so the same enum works for
every profession named in about_project.md.

## Shared error codes

Centralized in `shared/errors/codes.py` as `ErrorCode` (str enum), used in
API error responses, `WorkflowError.error_code`, and DLQ metadata. Per-domain
subsets:

| Code | Meaning |
|---|---|
| `VALIDATION_ERROR` | request/payload failed schema validation |
| `NOT_FOUND` | referenced entity does not exist |
| `UNAUTHORIZED` | caller lacks access to the resource |
| `RESUME_PARSE_FAILED` | resume text extraction or LLM parsing failed |
| `INVALID_JOB_URL` | submitted URL is not a fetchable/parseable job posting |
| `JOB_FETCH_FAILED` | network/extraction failure during ingestion |
| `NO_PROFILES_AVAILABLE` | user has no active `CandidateProfile` to match against |
| `MATCHING_FAILED` | matching workflow raised an unrecoverable error |
| `CONTACT_SEARCH_FAILED` | contact discovery tool/API call failed |
| `NO_CONTACTS_FOUND` | search completed with zero candidates |
| `OUTREACH_GENERATION_FAILED` | message generation workflow failed |
| `LLM_PROVIDER_ERROR` | upstream LLM call failed (any component) |
| `EXTERNAL_SEND_FAILED` | outreach send to email/LinkedIn provider failed |

Components may not define new error codes ad hoc for concepts already
covered above; a genuinely new failure mode is added here first.

## Versioning rules

- Every Kafka event payload is wrapped in `EventEnvelope[T]`, which carries
  `event_version: str` (semver-like, e.g. `"1.0"`) — see
  [event-contracts.md](event-contracts.md).
- **Additive, non-breaking change** (new optional field on an existing
  payload type): do not bump `event_version`. Consumers must ignore unknown
  fields (Pydantic models parse in permissive/ignore-extra mode for incoming
  events) and treat new optional fields as absent when reading old data.
- **Breaking change** (removing a field, renaming a field, changing a
  field's type or meaning, changing required-ness from optional to
  required): bump the major segment of `event_version`. The producer must
  support the old version for a defined deprecation window (dual-publish or
  dual-shape) until all known consumers upgrade; this is coordinated
  explicitly, not silently.
- The same additive-vs-breaking distinction applies to API request/response
  types and to database record schemas (migrations must be additive —
  nullable new columns, no in-place type changes without a migration plan
  reviewed by whoever owns the consuming components).
- Deprecated fields are marked in the type's docstring with the version they
  became deprecated in and are never removed in the same version they were
  deprecated in.
- Enum values are additive-only in normal operation. Removing or renaming an
  existing enum value is a breaking change under the same rule above,
  because every producer and consumer that pattern-matches on it must be
  updated in lockstep.
