# Repository Structure

Layout reflects the ownership boundaries in [ownership.md](ownership.md) —
one top-level package per component, plus `shared/`, `workflows/`,
`infrastructure/`, and `api/` as cross-cutting layers.

```
src/
├── shared/
│   ├── types/
│   │   ├── ids.py            # UserId, JobId, CorrelationId, ... (shared-types.md#identifiers)
│   │   ├── enums.py          # ResumeStatus, ApplicationStatus, ContactType, ... (shared-types.md#shared-enums)
│   │   ├── domain/           # CandidateProfile, Job, Contact, Outreach, Application, ... (domain-model.md)
│   │   ├── dto.py            # ResumeProfile, NormalizedJob, JobMatchResult,
│   │   │                     # ContactCandidate, ContactRankingResult, OutreachDraft,
│   │   │                     # ApplicationStatusUpdate (shared-types.md#canonical-exchange-types)
│   │   └── api/               # *Request / *Response types (api-contracts.md)
│   ├── events/
│   │   ├── envelope.py       # EventEnvelope[T], EventMetadata
│   │   └── payloads.py       # *Event payload types (event-contracts.md)
│   ├── errors/
│   │   └── codes.py          # ErrorCode (shared-types.md#shared-error-codes)
│   └── contracts/            # shared Protocol/ABC definitions (e.g. Repository base)
│
├── users/                    # User Service
│   ├── api/                  # /users, /users/{id}/preferences
│   ├── models.py             # UserRecord, UserPreferencesRecord
│   ├── repository.py
│   └── service.py
│
├── profiles/                 # Resume/Profile Service
│   ├── api/                  # /resumes, /profiles
│   ├── models.py             # ResumeRecord, CandidateProfileRecord
│   ├── repository.py
│   ├── parsing.py            # background parsing step (not LangGraph — see langgraph-state.md)
│   └── events.py             # producer: profiles.updated
│
├── jobs/
│   ├── models.py             # JobRecord, JobSourceRecord (shared schema, two writers — see database-ownership.md)
│   ├── ingestion/             # Job Ingestion Service
│   │   ├── api.py            # /jobs/ingest-url
│   │   ├── extraction.py     # Playwright/BeautifulSoup + LLM extraction
│   │   └── events.py         # producer: jobs.discovered (source_type=MANUAL_URL)
│   └── discovery/             # Job Discovery Service
│       ├── api.py            # /job-sources
│       ├── search.py
│       └── events.py         # producer: jobs.discovered (automatic sources)
│
├── matching/                 # Job Matching Service
│   ├── api/                  # /jobs, /jobs/{job_id}/matches
│   ├── models.py             # JobMatchRecord
│   ├── repository.py
│   ├── consumers.py          # jobs.discovered, profiles.updated
│   └── events.py             # producers: jobs.matched, jobs.shortlisted, contacts.requested
│
├── contacts/                 # Contact Discovery Service
│   ├── api/                  # /jobs/{job_id}/contacts, /jobs/{job_id}/contacts/search
│   ├── models.py             # ContactRecord, ContactScoreRecord
│   ├── repository.py
│   ├── consumers.py          # contacts.requested
│   └── events.py             # producer: contacts.found
│
├── outreach/                 # Outreach Service
│   ├── api/                  # /outreach, /outreach/{id}/approve|reject|edit
│   ├── models.py             # OutreachRecord
│   ├── repository.py
│   ├── consumers.py          # contacts.found; outreach.approved (send-worker group)
│   └── events.py             # producers: outreach.generated, outreach.approved, outreach.sent
│
├── tracking/                 # Tracking Service
│   ├── api/                  # /applications, /applications/{id}, /applications/{id}/status
│   ├── models.py             # ApplicationRecord, ApplicationHistoryRecord
│   ├── repository.py
│   ├── consumers.py          # all 7 upstream topics — see component-contracts.md
│   └── events.py             # producer: applications.updated
│
├── workflows/
│   └── langgraph/
│       ├── job_matching/
│       │   ├── state.py      # JobMatchingState
│       │   ├── nodes.py      # load_profiles, score_profile, select_best_profile, ...
│       │   └── graph.py
│       ├── contact_discovery/
│       │   ├── state.py      # ContactDiscoveryState
│       │   ├── nodes.py      # search_contacts, rank_contacts, persist_and_publish
│       │   └── graph.py
│       └── outreach_generation/
│           ├── state.py      # OutreachGenerationState
│           ├── nodes.py      # select_channel, generate_message, persist_and_publish
│           └── graph.py
│
├── infrastructure/
│   ├── kafka/                 # producer/consumer wrappers, envelope (de)serialization, retry/DLQ
│   ├── database/              # SQLAlchemy engine/session, Alembic migrations
│   ├── llm/                   # LLM Provider Layer (Ollama client, structured-output parsing)
│   └── external/              # Playwright/BeautifulSoup, people-search clients, email/LinkedIn send clients
│
└── api/
    └── main.py                # FastAPI app assembly — mounts each component's api/ router

tests/
    # mirrors src/ layout: one test module per component, plus
    # tests/integration/ for cross-component Kafka-flow tests

docs/
└── architecture/              # this directory
```

## What lives where, by concern

| Concern | Location |
|---|---|
| Shared contracts | `shared/` |
| Domain logic (one component) | that component's own top-level package |
| Agents / workflows | `workflows/langgraph/` |
| Infrastructure | `infrastructure/` |
| API layer | each component's `api/` submodule, assembled in `api/main.py` |
| Tests | `tests/`, mirroring `src/` |
| Documentation | `docs/` |

A component's package never contains another component's models or
repository code. A component's `api/` submodule only defines routes for
endpoints that component owns (per [api-contracts.md](api-contracts.md)) and
calls only its own `repository.py` and `service.py` — never another
component's `models.py` directly.

## Contract naming conventions

| Category | Convention | Examples |
|---|---|---|
| Identifiers | `<Entity>Id` | `JobId`, `UserId`, `ResumeId` |
| Domain types | bare noun matching the entity | `CandidateProfile`, `Job`, `Outreach` |
| Canonical exchange DTOs | descriptive noun, no entity suffix collision | `NormalizedJob`, `JobMatchResult`, `ResumeProfile` |
| API requests | `<Action><Entity>Request` | `CreateResumeRequest`, `IngestJobUrlRequest`, `UpdateApplicationStatusRequest` |
| API responses | `<Entity>Response` | `ResumeResponse`, `JobResponse`, `ApplicationResponse` |
| Commands (imperative, cause a state change) | `<Verb><Entity>Command` — used for internal command objects distinct from public API requests, where one exists | `MatchJobCommand` |
| Domain results | `<Entity>Result` | `JobMatchResult` |
| Kafka event payloads | `<EntityPastTense>Event` | `JobDiscoveredEvent`, `JobMatchedEvent`, `OutreachSentEvent` |
| Database entities | `<Entity>Record` | `ResumeRecord`, `JobMatchRecord`, `ApplicationRecord` |
| LangGraph state | `<Workflow>State` | `JobMatchingState`, `ContactDiscoveryState` |
| Errors | `<Domain>Error` locally; shared codes via `ErrorCode` | `MatchingError`, `ResumeParsingError` |
| Enums | plain noun, no `Enum`/`Type` suffix unless disambiguation is needed | `ResumeStatus`, `ContactType`, `OutreachChannel` |

**Avoid generic names** — `Data`, `Payload`, `Result` (unqualified),
`ResponseData`, `Info` — whenever a specific type can be named instead. If a
name doesn't tell you which entity or which layer (API/domain/event/DB/
LangGraph) it belongs to, it's wrong; see
[shared-types.md#type-layers-explicitly](shared-types.md#type-layers-explicitly).

## Versioning

Full policy in [shared-types.md#versioning-rules](shared-types.md#versioning-rules).
Applies uniformly to Kafka event payloads, API request/response types, and
database schemas: additive changes don't require a version bump; anything
that changes an existing field's meaning, type, or required-ness does.
