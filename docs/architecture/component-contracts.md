# Component Input/Output Contracts

This is the binding contract for every component boundary — trigger, input,
validation, output, destination, sync/async-ness, errors, and side effects.
An implementing agent should be able to build its component from this
document alone plus [shared-types.md](shared-types.md) and
[domain-model.md](domain-model.md), without reading another component's
code.

All Kafka producer/consumer entries reference topic mechanics documented
fully in [kafka-topics.md](kafka-topics.md); all event payloads reference
[event-contracts.md](event-contracts.md).

---

## Resume/Profile Service

### `POST /resumes` (upload)

```
Trigger: user uploads a resume file
Input Type: CreateResumeRequest
Fields (required): user_id: UserId, file_name: str, file_content: str (base64-encoded file bytes)
Fields (optional): none
Source: HTTP request body (JSON; file_content is the file base64-encoded
        client-side — see docs/frontend/api-mapping.md's "Wire-format note".
        MVP Blocker Fix update: previously documented as `bytes` and as
        "multipart", neither of which matched the actual/implemented
        contract; corrected here to match api-contracts.md#post-resumes and
        the frontend's already-implemented base64-JSON upload.)
Validation: file type in {pdf, docx, txt}; file content is valid base64;
            decoded file size <= 10MB; user_id exists
            (checked via User Service's `HEAD /users/{user_id}` — see
            api-contracts.md#user-service and dependency-graph.md)
Output Type: ResumeResponse
Destination: HTTP response
Sync/Async: synchronous for the upload+store step; parsing itself is async (see below)
Errors: VALIDATION_ERROR, NOT_FOUND (unknown user_id)
DB changes: INSERT resumes (status=UPLOADED)
Kafka events emitted: none directly — enqueues an internal parsing job
Status changes: Resume.status UPLOADED -> PARSING (immediately after insert)
```

### Resume Parsing Workflow (internal background step, not LangGraph — see langgraph-state.md)

```
Trigger: new Resume row with status=PARSING (enqueued by the upload handler)
Input Type: Resume (raw_text extraction target: storage_uri)
Source: resumes table (row it just inserted)
Validation: file is text-extractable; extracted text is non-empty
Output Type: CandidateProfile (domain) -> CandidateProfileRecord
Destination: candidate_profiles table
Sync/Async: asynchronous (background worker within the service; not a Kafka hop — see service-boundaries.md)
Errors: RESUME_PARSE_FAILED, LLM_PROVIDER_ERROR
DB changes: UPDATE resumes.status (PARSED or PARSE_FAILED), UPDATE resumes.parsed_at/parse_error,
            INSERT candidate_profiles
Kafka events emitted: ProfileUpdatedEvent -> profiles.updated (only on success)
Status changes: Resume.status PARSING -> PARSED | PARSE_FAILED
```

### `GET /resumes`, `GET /profiles` (read)

```
Trigger: caller requests current resumes/profiles for a user
Input Type: query param user_id: UserId
Output Type: list[ResumeResponse] / list[ResumeProfile]
Sync/Async: synchronous
Errors: NOT_FOUND
```

### `DELETE /resumes/{resume_id}`

```
Trigger: user removes a resume
Input Type: path param resume_id: ResumeId
Validation: resume belongs to the requesting user
Output Type: 204 No Content
DB changes: Resume.status -> ARCHIVED (soft delete; row retained for audit),
            associated CandidateProfile.status -> ARCHIVED
Kafka events emitted: ProfileUpdatedEvent (change_type=ARCHIVED) -> profiles.updated
Errors: NOT_FOUND
```

### Kafka Producer: `profiles.updated`

```
Emitted when: a CandidateProfile is created or archived
Payload Type: ProfileUpdatedEvent (wraps ProfileUpdateSummary)
Fields: profile_id, user_id, resume_id, change_type (CREATED | ARCHIVED), updated_at
Consumers: Job Matching Service (triggers re-matching of the user's open opportunities)
```

---

## Job Ingestion Service

### `POST /jobs/ingest-url`

```
Trigger: user pastes a job posting URL
Input Type: IngestJobUrlRequest
Fields (required): user_id: UserId, url: str
Fields (optional): none
Source: HTTP request body
Validation: url is well-formed and http(s); user_id exists
            (checked via User Service's `HEAD /users/{user_id}` — see
            api-contracts.md#user-service and dependency-graph.md)
Output Type: JobResponse (status=processing_status: NORMALIZED)
Destination: HTTP response
Sync/Async: synchronous — page fetch + extraction always complete within the request
            before any Job row is written. There is no persisted PENDING intermediate
            state: a row is inserted only once extraction succeeds (already at
            NORMALIZED), or no row is inserted at all and the request fails with an
            error below. This resolves a documented Wave 1 conflict between this
            endpoint's original PENDING-then-background-update design and
            database-ownership.md's exclusive grant of `processing_status` UPDATE
            rights to Job Matching Service (see state-machines.md#job-processing-lifecycle
            and database-ownership.md#jobs) — Job Ingestion Service may INSERT a Job row
            with its initial status, but never UPDATE processing_status after creation.
Errors: INVALID_JOB_URL, JOB_FETCH_FAILED, VALIDATION_ERROR
DB changes: INSERT jobs (source_type=MANUAL_URL, processing_status=NORMALIZED) on success only —
            no row is written on failure
Kafka events emitted: JobDiscoveredEvent -> jobs.discovered (once extraction succeeds)
Status changes: none — see state-machines.md#job-processing-lifecycle; this endpoint only
                sets the initial value at INSERT, which is not a status transition
```

### `GET /jobs/{job_id}`

```
Trigger: read request
Output Type: JobResponse
Sync/Async: synchronous
Errors: NOT_FOUND
```

Moved here from Job Matching Service, which does not own the `jobs` table
— see api-contracts.md#job-ingestion-service for the full gap resolution.

---

## Job Discovery Service

### Automatic Discovery Run (scheduled/internal trigger)

```
Trigger: scheduled run (e.g. periodic) per enabled JobSource, or manual trigger via
         a future admin action — no public API required by the current product scope
Input Type: JobSource (query_config) + UserPreferences (read via User Service API)
            + list[ResumeProfile] (read via Resume/Profile Service API, for relevance
            pre-filtering before publishing candidates)
Source: job_sources table (own), User Service API, Resume/Profile Service API
Validation: JobSource.enabled == true
Output Type: NormalizedJob (one per discovered posting)
Destination: Kafka topic jobs.discovered
Sync/Async: asynchronous background process
Errors: JOB_FETCH_FAILED, LLM_PROVIDER_ERROR (per-posting; a single failure doesn't stop the run)
DB changes: INSERT jobs (source_type=<source>, processing_status=NORMALIZED) per discovered
            posting; UPDATE job_sources.last_run_at
Kafka events emitted: JobDiscoveredEvent -> jobs.discovered (one per discovered job)
```

### `GET/POST /job-sources`

```
Trigger: user configures an automatic search source
Input Type: CreateJobSourceRequest / query
Output Type: JobSourceResponse
Sync/Async: synchronous
Errors: VALIDATION_ERROR
DB changes: INSERT/UPDATE job_sources
```

---

## Job Matching Service

### Kafka Consumer: `jobs.discovered`

```
Trigger: JobDiscoveredEvent consumed
Input Type: EventEnvelope[NormalizedJob]
Required fields: job_id, user_id, company, title, description, extracted_skills
Source: jobs.discovered topic
Validation: user_id has at least one CandidateProfile with status=ACTIVE
            (else short-circuit with NO_PROFILES_AVAILABLE: no job_matches row is
            inserted, no jobs.matched/jobs.shortlisted is published,
            Job.processing_status is set to FAILED — see
            langgraph-state.md#jobmatchingstate's persist_and_publish entry for the
            ratified terminal shape, and state-machines.md#job-processing-lifecycle)
Processing: runs the JobMatchingState LangGraph workflow (see langgraph-state.md) —
            fetches all ACTIVE ResumeProfile for the user, scores each against the
            NormalizedJob, selects the best, computes MatchRecommendation
Output Type: JobMatchResult
Destination:
    Kafka topic: jobs.matched (always)
    Kafka topic: jobs.shortlisted (only if recommendation == SHORTLIST)
    Kafka topic: contacts.requested (only if recommendation == SHORTLIST)
    Database: job_matches (insert), jobs.processing_status (update to MATCHED)
Sync/Async: asynchronous (Kafka consumer)
Errors: NO_PROFILES_AVAILABLE, MATCHING_FAILED, LLM_PROVIDER_ERROR
        (on error: WorkflowExecution.status=FAILED, no jobs.matched event published,
        message is retried per kafka-topics.md retry policy, then DLQ'd)
Status changes: Job.processing_status NORMALIZED -> MATCHED | FAILED — the only
                UPDATE transition any component performs on this column; see
                state-machines.md#job-processing-lifecycle
Idempotency: at-least-once redelivery of this message is handled via
             job_matches.published_at — see
             database-ownership.md#job_matches's "Publish-reliability
             column" note for the full three-way check (no row yet / row
             exists but not published / row exists and published) and why
             a full transactional outbox was not needed
```

### Kafka Consumer: `profiles.updated`

```
Trigger: ProfileUpdatedEvent consumed (change_type == CREATED)
Input Type: EventEnvelope[ProfileUpdateSummary]
Required fields: profile_id, user_id, resume_id
Source: profiles.updated topic
Processing: looks up the user's open opportunities (Job rows with
            processing_status in {NORMALIZED, MATCHED} that are not yet SHORTLISTED,
            or more precisely: jobs without an existing JobMatch referencing this
            profile) and re-runs the JobMatchingState workflow for each, now including
            the new profile as a candidate
Output Type: JobMatchResult (one per re-evaluated job, same shape and destinations as above)
Sync/Async: asynchronous
Errors: same as above
Notes: this consumer never re-evaluates jobs that already reached SHORTLISTED and
       moved past CONTACT_SEARCH in the Application lifecycle — re-matching only
       applies to jobs still awaiting a matching decision, to avoid retroactively
       reopening opportunities already in motion
```

### `GET /jobs/{job_id}/matches`

```
Trigger: read request
Output Type: JobMatchResponse
Sync/Async: synchronous
Errors: NOT_FOUND
```

`GET /jobs` (list) and `GET /jobs/{job_id}` (single-job read) previously
appeared here; moved to Job Ingestion Service (`GET /jobs/{job_id}`) or
dropped (`GET /jobs` list) — see api-contracts.md#job-matching-service for
the resolved gap and reasoning.

---

## Contact Discovery Service

### Kafka Consumer: `contacts.requested`

```
Trigger: ContactsRequestedEvent consumed
Input Type: EventEnvelope[ContactSearchRequest]
Required fields: job_id, user_id, company, title
Optional fields: location
Source: contacts.requested topic (producer: Job Matching Service on auto-shortlist,
        or this service's own POST /jobs/{job_id}/contacts/search on manual re-trigger)
Validation: job_id exists and belongs to user_id
Processing: runs the ContactDiscoveryState LangGraph workflow — search_contacts node
            (external people-search tools/APIs) then rank_contacts node (scoring against
            ContactScore signals) — both nodes execute in-process, no Kafka hop between them
Output Type: ContactRankingResult
Destination:
    Kafka topic: contacts.found
    Database: contacts (insert per candidate), contact_rankings (insert per candidate)
Sync/Async: asynchronous (Kafka consumer)
Errors: CONTACT_SEARCH_FAILED, NO_CONTACTS_FOUND, LLM_PROVIDER_ERROR
        (NO_CONTACTS_FOUND still publishes a ContactsFoundEvent with an empty contacts
        list, so Tracking can reflect "no contacts found" rather than silently stalling)
Status changes: Contact.status DISCOVERED -> RANKED for every persisted contact
```

### `POST /jobs/{job_id}/contacts/search` (manual re-trigger)

```
Trigger: user requests a fresh contact search for a job
Input Type: path param job_id: JobId
Validation: job_id exists, belongs to caller, and has Application.status >= SHORTLISTED
Output Type: 202 Accepted (ContactSearchRequest published)
Destination: Kafka topic contacts.requested (this endpoint is a thin producer, not a
             synchronous search — the result still arrives via contacts.found)
Sync/Async: input accepted synchronously, result asynchronous
Errors: NOT_FOUND, VALIDATION_ERROR
```

### `GET /jobs/{job_id}/contacts`

```
Trigger: read request
Output Type: list[ContactResponse] (includes relevance_score)
Sync/Async: synchronous
Errors: NOT_FOUND
```

---

## Outreach Service

### Kafka Consumer: `contacts.found`

```
Trigger: ContactsFoundEvent consumed
Input Type: EventEnvelope[ContactRankingResult]
Required fields: job_id, user_id, contacts (non-empty to proceed)
Source: contacts.found topic
Validation: if contacts is empty, no outreach is generated (this consumer no-ops;
            Application stays at CONTACT_SEARCH — the user may still apply directly)
Processing: for the top-ranked contact (highest relevance_score), runs the
            OutreachGenerationState LangGraph workflow — select_channel node then
            generate_message node, using the job's selected CandidateProfile/Resume
            (fetched via Resume/Profile Service API using selected_resume_id from the
            job's JobMatchResult, retrieved via Job Matching Service's read API)
Output Type: OutreachDraft
Destination:
    Kafka topic: outreach.generated
    Database: outreach (insert, status=PENDING_APPROVAL)
Sync/Async: asynchronous (Kafka consumer)
Errors: OUTREACH_GENERATION_FAILED, LLM_PROVIDER_ERROR
Status changes: Outreach.status DRAFT -> PENDING_APPROVAL
```

### `POST /outreach/{outreach_id}/approve`

```
Trigger: user approves a generated draft (human-in-the-loop gate)
Input Type: path param outreach_id: OutreachId; body ApproveOutreachRequest (optional
            final_message override)
Validation: Outreach.status == PENDING_APPROVAL; caller owns the outreach (via job's user_id)
Output Type: OutreachResponse
Destination:
    Kafka topic: outreach.approved
    Database: outreach.status, outreach.final_message, outreach.decided_at, outreach.decided_by
Sync/Async: synchronous API response; the actual send is asynchronous (see below)
Errors: VALIDATION_ERROR, NOT_FOUND
Status changes: Outreach.status PENDING_APPROVAL -> APPROVED (or EDITED if final_message differs
                from draft_message, tracked via the same status value plus a non-null final_message)
```

### `POST /outreach/{outreach_id}/reject`

```
Trigger: user rejects a generated draft
Input Type: path param outreach_id: OutreachId
Validation: Outreach.status == PENDING_APPROVAL
Output Type: OutreachResponse
Destination: Database only — outreach.status, outreach.decided_at, outreach.decided_by
Kafka events emitted: none (rejection is terminal and does not need to fan out beyond
                       Tracking, which already observes it via the outreach table's
                       absence of a downstream outreach.sent — see note below)
```

Note: rejection does not currently publish a dedicated event. Tracking Service
learns of it only indirectly (it never receives `outreach.sent` for that
`outreach_id`). This is flagged as a known gap: if Tracking needs to reflect
`OUTREACH_REJECTED` explicitly in the `Application` lifecycle in a future
iteration, add an `OutreachRejectedEvent` rather than inferring it from
absence. Not added now to avoid an unused topic (see
[kafka-topics.md](kafka-topics.md), "do not create topics unnecessarily").

### Kafka Consumer: `outreach.approved` (send worker, separate consumer group)

```
Trigger: OutreachApprovedEvent consumed
Input Type: EventEnvelope[OutreachDecision]
Required fields: outreach_id, decision (must be APPROVED to proceed)
Source: outreach.approved topic
Processing: sends the final_message via the provider matching Outreach.channel
            (email or LinkedIn provider)
Output Type: OutreachSentConfirmation
Destination:
    Kafka topic: outreach.sent
    Database: outreach.status, outreach.sent_at, outreach.external_message_id
Sync/Async: asynchronous (Kafka consumer, decoupled from the approval API request so
            external provider latency/rate limits never block the user's approval action)
Errors: EXTERNAL_SEND_FAILED (message retried per kafka-topics.md policy, then
        outreach.status -> SEND_FAILED with send_error populated, no outreach.sent published)
Status changes: Outreach.status APPROVED -> SENT | SEND_FAILED
```

---

## Tracking Service

Tracking Service has no LangGraph workflow and no external dependencies. It
is a pure event aggregator plus a read/update API. Every consumer below
performs the same shape of work: idempotent upsert into `applications`,
append-only insert into `application_history`, publish
`ApplicationUpdatedEvent`.

### Kafka Consumer: `jobs.discovered`

```
Input Type: EventEnvelope[NormalizedJob]
Processing: creates Application if none exists for job_id (first event for this job)
DB changes: INSERT applications (status=DISCOVERED), INSERT application_history
Kafka events emitted: ApplicationUpdatedEvent -> applications.updated
```

### Kafka Consumer: `jobs.matched`

```
Input Type: EventEnvelope[JobMatchResult]
Processing: updates Application.status, selected_resume_id, match_score, matched_skills,
            missing_skills — only if the incoming event's status represents forward
            progress (see state-machines.md for the idempotency/ordering rule)
DB changes: UPDATE applications, INSERT application_history
Kafka events emitted: ApplicationUpdatedEvent -> applications.updated
Status changes: Application.status -> MATCHED (or -> IGNORED if recommendation == IGNORE)
```

### Kafka Consumer: `jobs.shortlisted`

```
Input Type: EventEnvelope[JobMatchResult]
DB changes: UPDATE applications, INSERT application_history
Status changes: Application.status -> SHORTLISTED
Kafka events emitted: ApplicationUpdatedEvent -> applications.updated
```

### Kafka Consumer: `contacts.found`

```
Input Type: EventEnvelope[ContactRankingResult]
Processing: if contacts is non-empty, set referral_contact_id to the top-ranked contact
DB changes: UPDATE applications, INSERT application_history
Status changes: Application.status -> CONTACT_FOUND (or stays CONTACT_SEARCH if empty)
Kafka events emitted: ApplicationUpdatedEvent -> applications.updated
```

### Kafka Consumer: `outreach.generated`, `outreach.approved`, `outreach.sent`

```
Input Types: EventEnvelope[OutreachDraft] / EventEnvelope[OutreachDecision] / EventEnvelope[OutreachSentConfirmation]
DB changes: UPDATE applications, INSERT application_history (one per event)
Status changes: Application.status -> OUTREACH_GENERATED -> OUTREACH_APPROVED -> OUTREACH_SENT
Kafka events emitted: ApplicationUpdatedEvent -> applications.updated (once per consumed event)
```

### `PATCH /applications/{id}/status`

```
Trigger: user manually updates status (e.g. marking APPLIED, INTERVIEW, OFFER, REJECTED,
         or WITHDRAWN — statuses that happen outside the automated pipeline)
Input Type: UpdateApplicationStatusRequest
Fields (required): new_status: ApplicationStatus
Fields (optional): applied_date: date, notes: str
Validation: transition must be valid per state-machines.md (e.g. cannot go from
            DISCOVERED directly to OFFER)
Output Type: ApplicationResponse
DB changes: UPDATE applications, INSERT application_history (triggered_by="user")
Kafka events emitted: ApplicationUpdatedEvent -> applications.updated
Errors: VALIDATION_ERROR (invalid transition), NOT_FOUND
```

### `GET /applications`, `GET /applications/{id}`, `GET /applications/{id}/history`

```
Trigger: read request
Output Type: list[ApplicationResponse] / ApplicationResponse / list[ApplicationHistoryResponse]
Sync/Async: synchronous
Errors: NOT_FOUND
```
