# Service Boundaries

The logical services/modules of the platform. Each is a deployable-later
boundary within today's modular monolith (see
[overview.md#deployment-model](overview.md#deployment-model)). This is the
narrative counterpart to the flat lookup tables in
[ownership.md](ownership.md).

---

## User Service

**Responsibility:** account identity and search preferences.

- Input: `CreateUserRequest` / preference update requests (API)
- Output: `UserResponse`, `UserPreferences` (API)
- APIs exposed: `/users`, `/users/{id}/preferences`, `HEAD /users/{id}`
  (existence check, called by Resume/Profile Service and Job Ingestion
  Service — see their entries below)
- Kafka consumed: none
- Kafka produced: none
- DB entities owned: `User`, `UserPreferences`
- Dependencies: none
- Explicitly outside its responsibility: anything about *what* the user is
  professionally (that's Resume/Profile Service) or *which* jobs they've
  seen (that's Tracking Service).

---

## Resume/Profile Service

**Responsibility:** turn uploaded resumes into structured, profession-
agnostic candidate profiles. This is the component most directly responsible
for the platform's "generic, not hard-coded to software engineering" goal —
it has no profession-specific extraction logic; skills/roles/industries are
all free-text/list fields inferred per resume.

- Input: `CreateResumeRequest` (API); internally, `Resume.raw_text` for the
  parsing workflow
- Output: `ResumeResponse`, `ResumeProfile` (API); `ProfileUpdatedEvent` (Kafka)
- APIs exposed: `/resumes`, `/profiles`
- Kafka consumed: none
- Kafka produced: `profiles.updated`
- DB entities owned: `Resume`, `CandidateProfile`
- Dependencies: LLM Provider Layer (resume parsing/extraction), User
  Service (`HEAD /users/{id}`, validate `user_id` exists on upload)
- Explicitly outside its responsibility: deciding whether a profile is a
  good fit for any specific job (that's Job Matching Service); it only
  describes *who the user is*, never *what job to pursue*.

---

## Job Ingestion Service

**Responsibility:** the manual entry point — user pastes a URL, service
extracts a normalized job posting.

- Input: `IngestJobUrlRequest` (API)
- Output: `JobResponse` (API); `JobDiscoveredEvent` (Kafka)
- APIs exposed: `/jobs/ingest-url`, `GET /jobs/{job_id}` (single-job read —
  moved here from Job Matching Service, which does not own the `jobs`
  table; see api-contracts.md#job-ingestion-service)
- Kafka consumed: none
- Kafka produced: `jobs.discovered`
- DB entities owned: `Job` (create, `source_type=MANUAL_URL` only)
- Dependencies: External Integrations Layer (Playwright/BeautifulSoup page
  fetch), LLM Provider Layer (extraction), User Service (`HEAD
  /users/{id}`, validate `user_id` exists before ingesting)
- Explicitly outside its responsibility: deciding relevance or matching
  (that's Job Matching Service); automatic search (that's Job Discovery
  Service — the two services share the `Job` schema and the
  `jobs.discovered` topic but not code).

---

## Job Discovery Service

**Responsibility:** the automatic entry point — proactively find postings
using a user's profiles and preferences as search context.

- Input: `JobSource.query_config`, `UserPreferences` (via API),
  `list[ResumeProfile]` (via API)
- Output: `NormalizedJob` per discovered posting; `JobDiscoveredEvent` (Kafka)
- APIs exposed: `/job-sources`
- Kafka consumed: none
- Kafka produced: `jobs.discovered`
- DB entities owned: `Job` (create, non-manual `source_type`), `JobSource`
- Dependencies: External Integrations Layer (job board search
  APIs/scrapers), LLM Provider Layer, User Service (read preferences),
  Resume/Profile Service (read profiles)
- Explicitly outside its responsibility: deciding whether a discovered
  posting is actually worth pursuing — per about_project.md, "It should not
  make the final decision about whether a job is worth applying to. That
  responsibility belongs to the matching stage." This service only
  discovers; it never filters by relevance beyond very coarse source-level
  query parameters.

---

## Job Matching Service

**Responsibility:** compare a discovered job against every one of a user's
active profiles, select the best-fit resume, score the match, and gate
whether the opportunity proceeds (the "matching stage acts as a gate" rule
from about_project.md).

- Input: `EventEnvelope[NormalizedJob]` (from `jobs.discovered`),
  `EventEnvelope[ProfileUpdateSummary]` (from `profiles.updated`)
- Output: `JobMatchResult`; `JobMatchedEvent`, `JobShortlistedEvent`,
  `ContactsRequestedEvent` (Kafka)
- APIs exposed: `/jobs/{job_id}/matches` (read-only). `/jobs` (list) and
  `/jobs/{job_id}` previously appeared here; moved to Job Ingestion Service
  or dropped — see api-contracts.md#job-matching-service.
- Kafka consumed: `jobs.discovered`, `profiles.updated`
- Kafka produced: `jobs.matched`, `jobs.shortlisted`, `contacts.requested`
- DB entities owned: `JobMatch`; narrow update right on `Job.processing_status`
- Dependencies: Resume/Profile Service (read `ResumeProfile` list), User
  Service (read `UserPreferences`, assembled into `JobMatchingState` at
  workflow entry — see `langgraph-state.md#jobmatchingstate`), LLM
  Provider Layer (semantic scoring)
- Explicitly outside its responsibility: finding contacts, generating
  outreach, or tracking lifecycle state beyond its own `JobMatch` record —
  all of that is downstream, triggered by the events this service produces,
  never called directly.

---

## Contact Discovery Service

**Responsibility:** given a shortlisted job, find people at the target
company and rank them by referral/outreach relevance. Discovery and ranking
are two LangGraph nodes of one workflow inside this service (see
[langgraph-state.md](langgraph-state.md)), not two services — see
[kafka-topics.md](kafka-topics.md) for why no intermediate topic separates
them.

- Input: `EventEnvelope[ContactSearchRequest]` (from `contacts.requested`)
- Output: `ContactRankingResult`; `ContactsFoundEvent` (Kafka)
- APIs exposed: `/jobs/{job_id}/contacts`, `/jobs/{job_id}/contacts/search`
- Kafka consumed: `contacts.requested`
- Kafka produced: `contacts.found`
- DB entities owned: `Contact`, `ContactScore`
- Dependencies: External Integrations Layer (people-search APIs/tools),
  LLM Provider Layer
- Explicitly outside its responsibility: deciding *what* to say to a
  contact (that's Outreach Service) or whether the job itself was a good
  match (that's already decided upstream by the time this service runs).
  The contact type vocabulary it uses (`ContactType`) must stay
  profession-generic — no hard-coded "Software Engineer"/"Recruiter" logic
  branches; see [shared-types.md](shared-types.md#shared-enums).

---

## Outreach Service

**Responsibility:** generate personalized outreach for the top-ranked
contact, gate it behind human approval, and send it once approved. This is
where the human-in-the-loop requirement from CLAUDE.md is implemented as a
real, auditable state transition rather than a UI-only checkbox.

- Input: `EventEnvelope[ContactRankingResult]` (from `contacts.found`);
  `ApproveOutreachRequest` / reject request (API); `EventEnvelope[OutreachDecision]`
  (from its own `outreach.approved`, consumed by a separate send-worker
  consumer group)
- Output: `OutreachDraft`, `OutreachSentConfirmation`; `OutreachGeneratedEvent`,
  `OutreachApprovedEvent`, `OutreachSentEvent` (Kafka)
- APIs exposed: `/outreach`, `/outreach/{id}/approve`, `/outreach/{id}/reject`,
  `/outreach/{id}/edit`
- Kafka consumed: `contacts.found`, `outreach.approved` (own topic, send-worker group)
- Kafka produced: `outreach.generated`, `outreach.approved`, `outreach.sent`
- DB entities owned: `Outreach`
- Dependencies: Job Matching Service (`GET /jobs/{job_id}/matches`, read
  `selected_profile_id`/`selected_resume_id` — `contacts.found`'s payload
  doesn't carry them), Resume/Profile Service (read the selected
  resume/profile for tone/content, by the `profile_id` obtained from the
  call above), Job Ingestion Service (`GET /jobs/{job_id}`, read
  `company`/`title` for message personalization — no event payload this
  service consumes carries them either), LLM Provider Layer (message
  generation), External Integrations Layer (email/LinkedIn send providers)
- Explicitly outside its responsibility: deciding *who* to contact (that's
  Contact Discovery Service's ranking) and never sending anything without an
  `APPROVED` decision recorded first — this is a hard product constraint
  (about_project.md "Non-Goal": no mass/unsolicited outreach), enforced by
  the state machine in [state-machines.md](state-machines.md#outreach-lifecycle),
  not merely by convention.

---

## Tracking Service

**Responsibility:** the single source of truth for the user-facing
opportunity lifecycle. Aggregates events from every other component into
one `Application` record per job; never initiates work in other components.

- Input: `EventEnvelope[NormalizedJob | JobMatchResult | ContactRankingResult |
  OutreachDraft | OutreachDecision | OutreachSentConfirmation]` (from six
  topics); `UpdateApplicationStatusRequest` (API, for manual transitions)
- Output: `ApplicationResponse`, `ApplicationHistoryResponse`;
  `ApplicationUpdatedEvent` (Kafka)
- APIs exposed: `/applications`, `/applications/{id}`, `/applications/{id}/status`
- Kafka consumed: `jobs.discovered`, `jobs.matched`, `jobs.shortlisted`,
  `contacts.found`, `outreach.generated`, `outreach.approved`, `outreach.sent`
- Kafka produced: `applications.updated`
- DB entities owned: `Application`, `ApplicationHistory`
- Dependencies: none (deliberately — see below)
- Explicitly outside its responsibility: triggering any other component.
  Tracking never calls another service's API to make something happen; it
  only observes. This is what lets it safely consume from every other
  component (see [dependency-graph.md](dependency-graph.md)) without
  creating a dependency cycle back onto them.

---

## Kafka Infrastructure

**Responsibility:** the event backbone — not a business component, but a
shared piece of infrastructure every service depends on.

- Owns: topic definitions ([kafka-topics.md](kafka-topics.md)), the
  `EventEnvelope` (de)serialization wrapper, retry/DLQ plumbing,
  consumer-group naming convention
- Lives in: `infrastructure/kafka/`
- Depended on by: every component that produces or consumes an event (see
  [ownership.md](ownership.md#component--kafka-topics-produced))
- Explicitly outside its responsibility: any business logic about *what* an
  event means — that lives in the owning component's consumer handler, not
  in the infrastructure layer.

---

## LLM Provider Layer

**Responsibility:** centralized access to LLM inference (Ollama locally, per
about_project.md's technology table), so prompt construction, model
selection, and structured-output parsing are consistent and rate-limited in
one place rather than duplicated per component.

- Input: component-specific prompts + a target structured-output schema
  (e.g. a Pydantic model each caller wants back)
- Output: parsed structured response matching the caller's requested schema,
  or `LLM_PROVIDER_ERROR`
- APIs exposed: internal Python interface only (`infrastructure/llm/`), not
  an HTTP API — every LLM-calling component imports this layer directly, no
  event or network hop involved (an in-process call is the right tool here;
  see [overview.md](overview.md) rule 4 — this is a shared *contract*
  dependency, not a business dependency, so a direct import is fine)
- Depended on by: Resume/Profile Service, Job Ingestion Service, Job
  Discovery Service, Job Matching Service, Contact Discovery Service,
  Outreach Service
- Explicitly outside its responsibility: any domain-specific prompt content
  or business decision — it is a thin, swappable inference client, not a
  place for matching/ranking logic to leak into.

---

## Database Infrastructure

**Responsibility:** shared persistence plumbing — not a business
component, but infrastructure every component's own `repository.py`
depends on. Owned by the Database Agent
(`.claude/agents/database-agent.md`). Business table/entity ownership is
unaffected by this layer and stays exactly as defined in
[database-ownership.md](database-ownership.md) — this layer serves that
ownership model, it does not change it.

- Input: component-authored table/column definitions (each component's own
  `models.py`), migration requests from the owning component's agent
- Output: a working SQLAlchemy engine/session factory; Alembic migration
  tooling and execution
- APIs exposed: none — internal Python interface only
  (`infrastructure/database/`)
- Kafka consumed: none. Kafka produced: none.
- DB entities owned: none — see
  [ownership.md#infrastructure-ownership-non-business](ownership.md#infrastructure-ownership-non-business)
- Lives in: `infrastructure/database/`
- Depended on by: every component's own `repository.py` (see
  [dependency-graph.md](dependency-graph.md#4-database-dependencies))
- Explicitly outside its responsibility: any business table's schema,
  columns, or data — those are authored and owned by the component listed
  in [database-ownership.md](database-ownership.md). This layer never
  writes a component's `models.py` and never gains write access to a
  business table.

---

## External Integrations Layer

**Responsibility:** reusable third-party/external client adapters — the
transport-level code that talks to Playwright/BeautifulSoup, job board
search APIs/scrapers, people-search APIs/tools, and email/LinkedIn send
providers, so auth, retries, rate limiting, and response-parsing
primitives are centralized in one place rather than duplicated per
component. Owned by the External Integrations Agent
(`.claude/agents/external-integrations-agent.md`).

- Input: a URL to fetch, a search query/context, or a message + recipient
  + channel to send, from the calling component
- Output: raw/lightly-parsed page content, raw search results, or a send
  confirmation — plus a normalized error (e.g. `JOB_FETCH_FAILED`,
  `EXTERNAL_SEND_FAILED`) on failure
- APIs exposed: internal Python interface only
  (`infrastructure/external/`), not an HTTP API — the same in-process
  pattern as the LLM Provider Layer (see rule 4 above)
- Kafka consumed: none. Kafka produced: none.
- Depended on by: Job Ingestion Service (page fetch), Job Discovery
  Service (job board search), Contact Discovery Service (people-search),
  Outreach Service (email/LinkedIn send)
- Explicitly outside its responsibility: any business decision about
  *which* URL to fetch, *which* search query to run, *whether* a job or
  contact is relevant, or *what* message to send and to whom. Those
  decisions belong to Job Ingestion Service, Job Discovery Service,
  Contact Discovery Service, and Outreach Service respectively — this
  layer only supplies the client each of them calls, never job discovery,
  contact ranking, or outreach decision logic. It also never calls an LLM
  directly; that goes through the LLM Provider Layer above.
