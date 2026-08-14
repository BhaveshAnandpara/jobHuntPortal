# API Mapping

Every endpoint below is read directly from the mounted routers
(`src/*/api/routes.py`, `src/jobs/ingestion/api.py`,
`src/jobs/discovery/api.py`) and their request/response types
(`src/shared/types/api/*.py`), not just `api-contracts.md` prose — two real
prose/implementation mismatches were found this way (see
[Backend gaps](#backend-gaps-affecting-this-mapping) below). **No route
prefix exists beyond what's shown** — `app.include_router(...)` in
`src/api/main.py` mounts every router with no version prefix (e.g. not
`/api/v1/...`, despite `api-contracts.md`'s header note that a real
deployment would add one). The frontend's `api/client.ts` should read the
base URL from a single env var (`VITE_API_BASE_URL`) so adding a prefix
later is a one-line change, not a per-module edit.

## User Service — `src/api/users.ts`

| Frontend action | Method | Endpoint | Request | Response | State affected |
|---|---|---|---|---|---|
| Create identity | POST | `/users` | `CreateUserRequest` | `UserResponse` | Persist `id` to `localStorage`; seed `IdentityContext` |
| Load preferences | GET | `/users/{user_id}/preferences` | — | `UserPreferencesResponse` (404 if unset) | `useQuery(['preferences', userId])` |
| Save preferences | PUT | `/users/{user_id}/preferences` | `UpdateUserPreferencesRequest` | `UserPreferencesResponse` | Invalidate `['preferences', userId]` |

## Resume/Profile Service — `src/api/resumes.ts`, `src/api/profiles.ts`

| Frontend action | Method | Endpoint | Request | Response | State affected |
|---|---|---|---|---|---|
| List resumes | GET | `/resumes?user_id=` | — | `list[ResumeResponse]` | `useQuery(['resumes', userId])` |
| Upload resume | POST | `/resumes` | `CreateResumeRequest` — **see wire-format note below** | `ResumeResponse` (202) | Invalidate `['resumes', userId]`; begin polling that resume's status — see [async-workflows.md](async-workflows.md#resume-parsing) |
| Delete resume | DELETE | `/resumes/{resume_id}` | — | 204 | Invalidate `['resumes', userId]` and `['profiles', userId]` |
| List profiles | GET | `/profiles?user_id=` | — | `list[ResumeProfile]` | `useQuery(['profiles', userId])` |
| Get one profile | GET | `/profiles/{profile_id}` | — | `ResumeProfile` | `useQuery(['profile', profileId])` — used by Opportunity Detail's Selected Resume panel |

**Wire-format note:** `CreateResumeRequest.file_content` is typed `bytes`
on a JSON body (`src/shared/types/api/profiles.py:17-20`), not a
`multipart/form-data` upload. Pydantic/FastAPI decode a JSON `bytes` field
from a base64 string. The frontend must read the selected `File` via
`FileReader`/`arrayBuffer()` and base64-encode it client-side before
`JSON.stringify`-ing the request body — a plain `FormData` upload will
**not** match this contract. This is exactly the kind of integration
detail worth locking in architecture rather than letting the implementing
agent discover it by trial and error against a 422 response.

## Job Ingestion Service — `src/api/jobs.ts`

| Frontend action | Method | Endpoint | Request | Response | State affected |
|---|---|---|---|---|---|
| Submit job URL | POST | `/jobs/ingest-url` | `IngestJobUrlRequest` | `JobResponse` (202) | Invalidate `['applications', userId]` after a short delay/poll — see [user-flows.md#job-submission-flow](user-flows.md#job-submission-flow) for why this isn't a synchronous navigation |
| Get job detail | GET | `/jobs/{job_id}` | — | `JobResponse` — **⚠ see gap below, response is narrower than the panel needs** | `useQuery(['job', jobId])`, used by Opportunity Detail |

## Job Discovery Service — `src/api/jobSources.ts`

Brief section 3 (Job Submission UX) and the route table in
[routes.md](routes.md) intentionally do not surface Job Discovery Service
in this MVP frontend — `POST /job-sources` / `GET /job-sources` configure
*automatic* discovery, which is real backend functionality but not part of
the 11 numbered product-goal items in the Step 10 brief's section 2 (all
of which describe the manual paste-URL + review flow). Wiring it is a
**FUTURE ENHANCEMENT**, not a gap — the contract already exists
(`CreateJobSourceRequest` / `JobSourceResponse`) and needs no backend
change when it's picked up.

## Job Matching Service — `src/api/matching.ts`

| Frontend action | Method | Endpoint | Request | Response | State affected |
|---|---|---|---|---|---|
| Get match for a job | GET | `/jobs/{job_id}/matches` | — | `JobMatchResponse` — **⚠ see gap below, no `profile_scores`** | `useQuery(['match', jobId])`, used only for `recommendation` and `job_match_id`; score/matched/missing skills should be read from the already-fetched `ApplicationResponse` instead of duplicating the call's purpose |

## Contact Discovery Service — `src/api/contacts.ts`

| Frontend action | Method | Endpoint | Request | Response | State affected |
|---|---|---|---|---|---|
| List contacts for a job | GET | `/jobs/{job_id}/contacts` | — | `list[ContactResponse]` (always 200, empty list is valid) | `useQuery(['contacts', jobId])` |
| Manually re-trigger search | POST | `/jobs/{job_id}/contacts/search` | `TriggerContactSearchRequest { user_id, company, title, location }` | `TriggerContactSearchResponse` (202) | Invalidate `['contacts', jobId]` after a delay; **the frontend must supply `company`/`title`/`location` itself** (from the already-fetched job/application data) — Contact Discovery Service cannot look these up (see `api-contracts.md`'s note on this endpoint's resolved gap) |

This manual re-trigger action is a small, real, already-supported
capability not explicitly called out in the brief's page list — worth
surfacing as a "Search again" button on the Contacts panel for the case
where the automated search found nothing on the first pass, since the
contract already supports it and doing so requires no new backend work.

## Outreach Service — `src/api/outreach.ts`

| Frontend action | Method | Endpoint | Request | Response | State affected |
|---|---|---|---|---|---|
| List outreach | GET | `/outreach?user_id=&status=` | — | `list[OutreachResponse]` | `useQuery(['outreach', userId, status])` |
| Get one outreach | GET | `/outreach/{outreach_id}` | — | `OutreachResponse` | `useQuery(['outreach-item', outreachId])` |
| Approve | POST | `/outreach/{outreach_id}/approve` | `ApproveOutreachRequest { final_message }` | `OutreachResponse` (200/409) | Invalidate `['outreach', ...]`, `['outreach-item', id]` |
| Reject | POST | `/outreach/{outreach_id}/reject` | — | `OutreachResponse` (200/409) | Same invalidation |
| Edit draft | POST | `/outreach/{outreach_id}/edit` | `EditOutreachRequest { message }` | `OutreachResponse` (200/409) | Same invalidation |

**⚠ Minor gap:** there is no `job_id` query filter on `GET /outreach` —
only `user_id` and `status`. The Opportunity Detail page's outreach panel
must fetch the user's full outreach list and filter client-side by
`job_id` (both fields are present on `OutreachResponse`). **NON-BLOCKING**
— functionally fine at MVP data volumes; if a user's outreach list grows
very large, a `job_id` filter param would be a small, additive backend
improvement worth requesting later, not now.

## Tracking Service — `src/api/tracking.ts`

| Frontend action | Method | Endpoint | Request | Response | State affected |
|---|---|---|---|---|---|
| List opportunities | GET | `/applications?user_id=&status=` | — | `list[ApplicationResponse]` | `useQuery(['applications', userId, status])` — backs both the Dashboard summary and the Opportunities list |
| Get one opportunity | GET | `/applications/{application_id}` | — | `ApplicationResponse` | `useQuery(['application', id])` |
| Manually change status | PATCH | `/applications/{application_id}/status` | `UpdateApplicationStatusRequest { new_status, applied_date, notes }` | `ApplicationResponse` (200/400) | Invalidate `['application', id]`, `['applications', ...]`, `['history', id]` |
| Get lifecycle history | GET | `/applications/{application_id}/history` | — | `list[ApplicationHistoryResponse]` | `useQuery(['history', id])` |

---

## Backend gaps affecting this mapping

1. **✅ RESOLVED (Step 10.5)** — `JobResponse` now carries
   `location`/`description`/`extracted_skills`/`experience_required`/
   `source_url`, projected from the already-persisted `Job` domain entity.
   Opportunity Detail's Job Information panel needs no workaround.
2. **✅ RESOLVED (Step 10.5)** — `JobMatchResponse` now carries
   `profile_scores`, projected from the already-persisted
   `JobMatch.profile_scores`, reusing the canonical `ProfileMatchScore`
   shape. The "other resumes were evaluated" comparison needs no
   workaround.
3. **No `job_id` filter on `GET /outreach`** (NON-BLOCKING, still open) —
   see above. Not in scope for Step 10.5 — both fixed gaps there were
   specifically the two classified as BLOCKER; this one was and remains
   NON-BLOCKING, left for a later pass if it proves to matter at real data
   volumes.

Both blockers were closed as the smallest possible additive changes (new
response fields projecting already-persisted domain data — no new domain
fields, no schema change, no behavior change) rather than worked around in
the frontend, once a dedicated backend-contract-cleanup pass (Step 10.5)
was explicitly scoped for exactly this.

## Error contract (applies to every table above)

Verified via a full grep of every `detail=` construction across all eight
routers (`users`, `profiles`, `jobs/ingestion`, `jobs/discovery`,
`matching`, `contacts`, `outreach`, `tracking`): **100% consistent** —
every `4xx` is `HTTPException(detail={"code": ErrorCode, "message": str})`,
i.e. the JSON body is always `{"detail": {"code": "...", "message": "..."}}`.
`ErrorCode` is the shared, locked enum (`src/shared/errors/codes.py`). The
frontend's `api/client.ts` can therefore parse this one shape into a single
typed `ApiError` used everywhere (see [error-handling.md](error-handling.md))
with no per-endpoint special-casing.
