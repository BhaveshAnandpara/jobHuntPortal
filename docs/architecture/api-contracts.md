# API Boundaries

Contracts only — no implementation. All request/response types are Pydantic
models under `shared/types/api/`, named per
[contract-naming-conventions](#contract-naming-conventions-cross-reference).
All endpoints are prefixed per owning service in the real deployment (e.g.
`/api/v1/...`); prefixes are omitted below for brevity.

## User Service

### `HEAD /users/{user_id}`

```
Purpose: existence check only — lets another component validate a user_id
         before writing, per ownership.md's "prefer a contract over
         reaching into internal state" rule, without fetching or exposing
         any User fields to the caller
Path params: user_id: UserId
Response: no body
Status codes: 200 (exists), 404 (does not exist)
Errors: NOT_FOUND
DB state affected: none
```

This is the internal existence-check contract referenced by Resume/Profile
Service's `POST /resumes` and Job Ingestion Service's `POST /jobs/ingest-url`
(see their `component-contracts.md` entries) to satisfy their documented
"user_id exists" validation rule. A `HEAD` request with a plain status code
was chosen over a full `GET /users/{user_id}` (which would need a new
`UserResponse`-shaped read) because callers only need a boolean, not the
`User` entity's fields — see `dependency-graph.md`'s runtime API
dependencies for the resulting call edges.

### `POST /users`

```
Purpose: create a user account
Request body: CreateUserRequest { email: str, display_name: str, timezone: str | None }
Response: UserResponse { id: UserId, email: str, display_name: str, created_at: datetime }
Status codes: 201, 400
Errors: VALIDATION_ERROR
Events generated: none
DB state affected: INSERT users
```

### `GET /users/{user_id}/preferences`

```
Purpose: read search preferences
Path params: user_id: UserId
Response: UserPreferencesResponse
Status codes: 200, 404
Errors: NOT_FOUND
DB state affected: none
```

### `PUT /users/{user_id}/preferences`

```
Purpose: replace search preferences
Path params: user_id: UserId
Request body: UpdateUserPreferencesRequest { target_roles, target_locations,
              remote_preference, excluded_companies, min_salary, salary_currency }
Response: UserPreferencesResponse
Status codes: 200, 400, 404
Errors: VALIDATION_ERROR, NOT_FOUND
Events generated: none
DB state affected: UPSERT user_preferences
```

## Resume/Profile Service

### `POST /resumes`

```
Purpose: upload a resume, triggering profile parsing
Request body: CreateResumeRequest { user_id: UserId, file_name: str, file_content: str }
Response: ResumeResponse { id, user_id, file_name, status: ResumeStatus, uploaded_at }
Status codes: 202, 400, 404
Errors: VALIDATION_ERROR, NOT_FOUND
Owning service: Resume/Profile Service
Events generated: ProfileUpdatedEvent (async, once parsing completes)
DB state affected: INSERT resumes; later INSERT candidate_profiles
```

**MVP Blocker Fix update:** `file_content` was previously documented (and
implemented) as `bytes`. Plain Pydantic `bytes` does **not** base64-decode a
JSON string — it UTF-8-encodes the string's own characters — so the
documented wire format (a base64 string inside JSON, per
`docs/frontend/api-mapping.md`'s "Wire-format note", already implemented by
the frontend) was silently corrupting every uploaded resume's content in
real (non-fake-LLM) usage. Retyped to `str` (still the same base64 string on
the wire — no frontend change needed) with explicit
`base64.b64decode(..., validate=True)` inside
`ProfileService.upload_resume()`, raising `ErrorCode.VALIDATION_ERROR` on
malformed input through the existing normalized-error path. No endpoint,
status code, or wire-format change — this corrects the request model's type
to match the contract that was already documented and already relied upon.

### `GET /resumes`

```
Purpose: list a user's resumes
Query params: user_id: UserId
Response: list[ResumeResponse]
Status codes: 200
DB state affected: none
```

### `DELETE /resumes/{resume_id}`

```
Purpose: remove (archive) a resume and its derived profile
Path params: resume_id: ResumeId
Response: 204 No Content
Status codes: 204, 404
Errors: NOT_FOUND
Events generated: ProfileUpdatedEvent (change_type=ARCHIVED)
DB state affected: UPDATE resumes.status=ARCHIVED, UPDATE candidate_profiles.status=ARCHIVED
```

### `GET /profiles`

```
Purpose: list a user's derived candidate profiles
Query params: user_id: UserId
Response: list[ResumeProfile]
Status codes: 200
DB state affected: none
```

### `GET /profiles/{profile_id}`

```
Purpose: read one profile
Path params: profile_id: ProfileId
Response: ResumeProfile
Status codes: 200, 404
Errors: NOT_FOUND
```

## Job Ingestion Service

### `POST /jobs/ingest-url`

```
Purpose: submit a job posting URL for extraction
Request body: IngestJobUrlRequest { user_id: UserId, url: str }
Response: JobResponse { id, user_id, company, title, location, description,
          extracted_skills, experience_required, source_url,
          processing_status, discovered_at }
Status codes: 202, 400, 404
Errors: INVALID_JOB_URL, VALIDATION_ERROR, NOT_FOUND
Owning service: Job Ingestion Service
Events generated: JobDiscoveredEvent (once extraction succeeds)
DB state affected: INSERT jobs
```

### `GET /jobs/{job_id}`

```
Purpose: read one job's full canonical detail (company, title, location,
         description, extracted_skills, experience_required, source_url,
         processing_status) — the read path for a UI/dashboard to display
         the actual job posting a user pasted or that was discovered, since
         no other component's read surface carries the raw posting text
         (Tracking Service's Application only denormalizes company/title;
         Job Matching Service's own JobMatchResult carries matched/missing
         skills, not the posting itself)
Path params: job_id: JobId
Response: JobResponse { id, user_id, company, title, location, description,
          extracted_skills, experience_required, source_url,
          processing_status, discovered_at }
Status codes: 200, 404
Errors: NOT_FOUND
Owning service: Job Ingestion Service (the `jobs` table's two writers share
                one `jobs/` package and one repository per
                repository-structure.md — this read endpoint is mounted
                once, at the package level, not duplicated per writer)
DB state affected: none
```

Resolves the gap flagged in the Job Matching Service implementation report:
`GET /jobs` and `GET /jobs/{job_id}` were previously (incorrectly)
documented under Job Matching Service, which does not own the `jobs`
table and has no documented read path to it
(`database-ownership.md#jobs`, `ownership.md`'s "never a direct
cross-component table read" rule). Moved to the actual data owner. Only
the single-job read was added — see the Job Matching Service section below
for why a `GET /jobs` list endpoint was deliberately not added.

**Step 10.5 update:** this Purpose text always documented
`location`/`description`/`extracted_skills`/`experience_required`/
`source_url` as part of the response, but `JobResponse`
(`shared/types/api/jobs.py`) didn't actually carry them until this pass —
flagged as a frontend-blocking gap in `docs/frontend/routes.md` and closed
here by projecting the already-persisted `Job` fields onto the response
type. No new domain data, no schema change.

## Job Discovery Service

### `POST /job-sources`

```
Purpose: configure an automatic discovery source
Request body: CreateJobSourceRequest { user_id: UserId, name: str, type: JobSourceType,
              query_config: dict, enabled: bool }
Response: JobSourceResponse
Status codes: 201, 400
Errors: VALIDATION_ERROR
DB state affected: INSERT job_sources
```

### `GET /job-sources`

```
Purpose: list a user's configured sources
Query params: user_id: UserId
Response: list[JobSourceResponse]
Status codes: 200
```

## Job Matching Service

`GET /jobs` and `GET /jobs/{job_id}` previously appeared in this section;
both have moved to Job Ingestion Service above (`GET /jobs/{job_id}`) or
been dropped entirely (`GET /jobs` list — see below), since Job Matching
Service does not own the `jobs` table and had no documented, non-violating
way to serve them (flagged as a gap in the Job Matching Service
implementation report, resolved here). A `GET /jobs` **list** endpoint was
deliberately not added anywhere: Tracking Service's existing
`GET /applications` (`api-contracts.md#tracking-service`) already provides
the "browse my jobs" list view a dashboard needs — one row per job,
denormalized company/title, plus match/status/lifecycle information a bare
`Job` listing wouldn't carry — so adding a second, narrower listing
endpoint over the same underlying jobs would be a duplicate read surface
for no added capability (`ownership.md`'s "prefer a contract over reaching
into internal state" spirit extends to preferring an existing contract
over adding a redundant one). A dashboard combines `GET /applications`
(list + status) with `GET /jobs/{job_id}` (full posting detail on demand)
rather than needing a third combined endpoint.

### `GET /jobs/{job_id}/matches`

```
Purpose: read the matching result for a job
Path params: job_id: JobId
Response: JobMatchResponse { job_match_id, selected_profile_id, selected_resume_id,
          match_score, matched_skills, missing_skills, recommendation,
          profile_scores, matched_at }
Status codes: 200, 404
Errors: NOT_FOUND
DB state affected: none
```

**Step 10.5 update:** `profile_scores` (list of `ProfileMatchScore`, the
same canonical shape already used on the persisted `JobMatch` domain
entity — `domain-model.md`'s JobMatch fields, `shared/types/dto.py`) was
added to expose the score against every evaluated profile, not just the
winner — the data was already persisted (`job_matches.profile_scores`) but
not previously projected onto this response type. Flagged as a
frontend-blocking gap in `docs/frontend/routes.md`
("other resumes were evaluated," brief section 7). No second scoring
representation was introduced — this reuses `ProfileMatchScore` exactly as
`JobMatch.profile_scores` already defines it.

## Contact Discovery Service

### `GET /jobs/{job_id}/contacts`

```
Purpose: list ranked contacts found for a job
Path params: job_id: JobId
Response: list[ContactResponse] { id, full_name, headline, company, contact_type,
          profile_url, relevance_score, status }
Status codes: 200
Errors: none
DB state affected: none
```

Resolved gap (Wave 2 Contact Discovery implementation): the documented `404
NOT_FOUND` was not actually reachable — Contact Discovery Service has zero
outbound API calls (`dependency-graph.md#2-runtime-api-dependencies`) and no
read access to `jobs`/`applications` (`database-ownership.md`), so it has no
way to distinguish "unknown `job_id`" from "no contacts found/searched yet
for a real job". Both cases return `200` with an empty list — consistent
with `contacts.found`'s own "empty list is a valid result, not an error"
contract (`event-contracts.md#contactsfoundevent`).

### `POST /jobs/{job_id}/contacts/search`

```
Purpose: manually (re-)trigger contact discovery for a job
Path params: job_id: JobId
Request body: TriggerContactSearchRequest { user_id: UserId, company: str,
              title: str, location: str | None }
Response: 202 Accepted { job_id, requested_at }
Status codes: 202, 400
Errors: VALIDATION_ERROR (request body fails schema validation)
Owning service: Contact Discovery Service
Events generated: ContactsRequestedEvent
DB state affected: none directly (result arrives asynchronously via contacts.found)
```

Resolved gaps (Wave 2 Contact Discovery implementation), both stemming from
the same zero-outbound-call constraint as above:

- **Request body added.** The event this endpoint must publish
  (`ContactSearchRequest`) requires `user_id`/`company`/`title` — data that
  lives on `jobs` (Job Ingestion/Job Discovery-owned) and this service
  cannot look up itself. Rather than leave the endpoint unable to fulfill
  its own documented contract, `TriggerContactSearchRequest` was added as
  an additive request body (`shared/types/api/contacts.py`) — the caller
  (e.g. a dashboard that already fetched the job via Job Ingestion
  Service's `GET /jobs/{job_id}`) supplies these fields directly. This does
  **not** add a runtime API dependency for Contact Discovery Service itself
  — the caller/UI does that lookup, not this service — so
  `dependency-graph.md`'s "Contact Discovery Service makes zero runtime API
  calls" remains true.
- **`Application.status >= SHORTLISTED` validation dropped.** Not
  checkable here for the same reason (no read path to `applications`,
  Tracking-owned, and no permitted synchronous call to Tracking Service).
  The endpoint accepts any well-formed `job_id`; `404`/that specific
  `VALIDATION_ERROR` case are removed from this contract accordingly. If
  this check is genuinely needed later, it belongs on the caller (which can
  already read `Application.status` via Tracking's `GET
  /applications/{id}`), not as a new Contact Discovery Service dependency.

## Outreach Service

### `GET /outreach`

```
Purpose: list outreach drafts/records for a user
Query params: user_id: UserId, status: OutreachStatus | None
Response: list[OutreachResponse]
Status codes: 200
```

### `GET /outreach/{outreach_id}`

```
Purpose: read one outreach record
Path params: outreach_id: OutreachId
Response: OutreachResponse { id, job_id, contact_id, channel, draft_message,
          final_message, status, generated_at, decided_at, sent_at }
Status codes: 200, 404
Errors: NOT_FOUND
```

### `POST /outreach/{outreach_id}/approve`

```
Purpose: human approval gate — authorize sending
Path params: outreach_id: OutreachId
Request body: ApproveOutreachRequest { final_message: str | None }
Response: OutreachResponse
Status codes: 200, 400, 404, 409
Errors: VALIDATION_ERROR, NOT_FOUND, and a 409-mapped error if
        status != PENDING_APPROVAL (already decided)
Owning service: Outreach Service
Events generated: OutreachApprovedEvent
DB state affected: UPDATE outreach.status, final_message, decided_at, decided_by
```

### `POST /outreach/{outreach_id}/reject`

```
Purpose: human approval gate — decline sending
Path params: outreach_id: OutreachId
Response: OutreachResponse
Status codes: 200, 404, 409
Errors: NOT_FOUND, 409 if status != PENDING_APPROVAL
Events generated: none (see kafka-topics.md's note on the deferred outreach.rejected topic)
DB state affected: UPDATE outreach.status=REJECTED, decided_at, decided_by
```

### `POST /outreach/{outreach_id}/edit`

```
Purpose: edit the draft message before approving
Path params: outreach_id: OutreachId
Request body: EditOutreachRequest { message: str }
Response: OutreachResponse
Status codes: 200, 400, 404, 409
Errors: VALIDATION_ERROR, NOT_FOUND, 409 if status != PENDING_APPROVAL
DB state affected: UPDATE outreach.final_message, status=EDITED
```

## Tracking Service

### `GET /applications`

```
Purpose: list all tracked opportunities for a user
Query params: user_id: UserId, status: ApplicationStatus | None
Response: list[ApplicationResponse]
Status codes: 200
DB state affected: none
```

### `GET /applications/{application_id}`

```
Purpose: read one tracked opportunity
Path params: application_id: ApplicationId
Response: ApplicationResponse (full entity, see domain-model.md#application)
Status codes: 200, 404
Errors: NOT_FOUND
```

### `PATCH /applications/{application_id}/status`

```
Purpose: manually advance/correct an opportunity's status
Path params: application_id: ApplicationId
Request body: UpdateApplicationStatusRequest { new_status: ApplicationStatus,
              applied_date: date | None, notes: str | None }
Response: ApplicationResponse
Status codes: 200, 400, 404
Errors: VALIDATION_ERROR (invalid transition — see state-machines.md), NOT_FOUND
Owning service: Tracking Service
Events generated: ApplicationUpdatedEvent
DB state affected: UPDATE applications, INSERT application_history
```

### `GET /applications/{application_id}/history`

```
Purpose: full audit trail for one opportunity
Path params: application_id: ApplicationId
Response: list[ApplicationHistoryResponse]
Status codes: 200, 404
Errors: NOT_FOUND
DB state affected: none
```

## Contract naming conventions (cross-reference)

See [repository-structure.md#contract-naming-conventions](repository-structure.md#contract-naming-conventions)
for the full rules. In short: every request type ends in `Request`, every
response type ends in `Response`, and a response type's fields never
introduce a new representation of an entity already defined in
[domain-model.md](domain-model.md) — it's either that entity's fields
verbatim or an explicit named subset.
