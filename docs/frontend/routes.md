# Information Architecture

## Why this differs from the example route list in the Step 10 brief

The brief's example listed separate `/jobs` and `/applications` pages. The
actual backend does not support that split — and deliberately so, per
`api-contracts.md`'s own resolved-gap note under Job Matching Service:

> A `GET /jobs` list endpoint was deliberately not added anywhere:
> Tracking Service's existing `GET /applications` already provides the
> "browse my jobs" list view a dashboard needs... A dashboard combines
> `GET /applications` (list + status) with `GET /jobs/{job_id}` (full
> posting detail on demand) rather than needing a third combined endpoint.

So there is exactly **one** list surface for "things I'm pursuing" —
`GET /applications` — not two. Building separate `/jobs` and
`/applications` pages would mean either inventing a client-side "jobs list"
out of an endpoint that doesn't exist, or rendering the same
`GET /applications` data twice under two names. Both are worse than one
page. This document therefore uses a single **Opportunities** page (list +
detail) as the backend actually shapes it, using product language already
established in `about_project.md` ("Opportunity Lifecycle", "Opportunity
Filtering").

A related, non-obvious consequence: the detail route is keyed by
**`applicationId`**, not `jobId`. `GET /applications` returns
`ApplicationResponse` objects, each carrying both `id` (`ApplicationId`)
and `job_id` (`JobId`). The frontend already has the full `ApplicationResponse`
in hand when the user clicks a list row, so routing on `applicationId` and
then reading `.job_id` off the already-fetched object to make secondary
calls (job detail, matches, contacts) avoids adding any new backend lookup
("get application by job_id" does not exist and should not be invented for
this).

## Route table

| Route | Page | Purpose |
|---|---|---|
| `/welcome` | Onboarding | First-run identity creation. Redirects to `/` if an identity already exists in local storage. |
| `/` | Dashboard | Primary action (submit a job URL) + pipeline summary + things needing attention. |
| `/resumes` | Resume Management | Upload/list/delete resumes; view derived profiles. |
| `/opportunities` | Opportunities | The full pipeline list — filterable by status. This *is* the application tracker. |
| `/opportunities/:applicationId` | Opportunity Detail | Job info, match, contacts, outreach, lifecycle history for one opportunity. |
| `/outreach` | Outreach Queue | Cross-opportunity queue of outreach needing a decision, plus history. |
| `/outreach/:outreachId` | Outreach Review (deep link) | Same review panel as the queue, addressable directly (e.g. from a Dashboard "needs attention" link). |
| `/settings` | Search Preferences | `UserPreferences` only — see note below on why this isn't a general "profile settings" page. |

Note on `/settings`: the backend has no "update user" endpoint (only
`POST /users` create — see `api-contracts.md#user-service`). There is
nothing else to edit about the user's identity itself. `/settings` is
scoped to `UserPreferencesResponse`'s fields only; renaming/re-editing
display name or email is a **FUTURE ENHANCEMENT** gap, not something this
page can offer (see [api-mapping.md](api-mapping.md)).

---

## `/welcome` — Onboarding

**Purpose:** create the local identity used for every subsequent API call.

**Backend APIs:** `POST /users`.

**Major components:** `CreateIdentityForm` (email, display name, timezone
— optional).

**User actions:** submit form → identity created → persisted to
`localStorage` → redirect to `/`.

**Loading:** submit button spinner during the create call.
**Empty:** N/A (this page has no data to be empty).
**Error:** inline field error on `400 VALIDATION_ERROR` (e.g. malformed
email); banner on network failure with retry.

---

## `/` — Dashboard

**Purpose:** the single place a returning user starts. Optimized for
action, not analytics (per brief section 13).

**Backend APIs:**
- `GET /applications?user_id=` (for pipeline summary + recent list — one
  call, sliced client-side into "active", "shortlisted", "awaiting
  outreach approval", "interview")
- `GET /outreach?user_id=&status=PENDING_APPROVAL` and
  `status=EDITED` (for the "needs attention" outreach count/list)
- `POST /jobs/ingest-url` (the primary action)

**Major components:** `JobUrlSubmitForm` (the one prominent input on the
page), `PipelineSummaryCards`, `NeedsAttentionList`, `RecentOpportunities`.

**User actions:** paste a URL and submit (see
[user-flows.md#job-submission-flow](user-flows.md#job-submission-flow));
click through to `/opportunities` or `/outreach` from any summary card.

**Loading:** skeleton cards for summary counts while `GET /applications`
resolves; the URL form is interactive immediately (it doesn't depend on
this data).
**Empty:** first-run state ("No opportunities yet — paste a job URL to get
started") replaces the summary cards entirely when the list is empty; this
is the expected state right after `/welcome`.
**Error:** summary cards show a retry affordance if `GET /applications`
fails; the URL submit form has its own independent error state (see
[user-flows.md#job-submission-flow](user-flows.md#job-submission-flow))
so one failing panel never blocks the other.

---

## `/resumes` — Resume Management

**Purpose:** the full multi-resume lifecycle described in brief section 7.

**Backend APIs:**
- `GET /resumes?user_id=`
- `POST /resumes` (upload — see the wire-format note in
  [api-mapping.md](api-mapping.md#resumeprofile-service))
- `DELETE /resumes/{resume_id}`
- `GET /profiles?user_id=` (to show each resume's derived profile summary
  alongside it — a `Resume` and its `CandidateProfile` are 1:1 but two
  separate reads)

**Major components:** `ResumeUploadButton`, `ResumeList` (each row:
file name, status badge, upload date), `ResumeProfileSummary` (skills,
seniority, title — expandable per row), `DeleteResumeConfirm`.

**User actions:** upload (multi-select allowed — one `POST /resumes` per
file), delete (with confirmation, since it also archives the derived
profile), no "replace" endpoint exists — see note below.

**Note on "replace a resume":** the brief's section 7 asks for a replace
action. The backend has no dedicated replace endpoint — "replace" is
`DELETE` the old resume, then `POST` a new one (two calls, two `Resume`
rows, matching `state-machines.md#resume-lifecycle`'s explicit "retry is a
new upload... not a re-attempt on the failed one"). The UI can offer a
single "Replace" button that performs both calls in sequence as a UX
convenience — this is composition of existing contracts, not new backend
behavior, so it stays in scope.

**Loading:** skeleton rows while resumes load; per-row "Parsing..." status
badge (from `ResumeStatus.PARSING`) while a just-uploaded resume is still
being analyzed — see
[async-workflows.md#resume-parsing](async-workflows.md#resume-parsing-progressive-disclosure).
**Empty:** "No resumes yet" with the upload action prominent (this is the
expected state at first run, and re-used inside `/welcome` if onboarding is
extended later — not duplicated markup, just the same `EmptyState`
component).
**Error:** per-file upload failure shows inline on that file's row (not a
page-level error, since other uploads may have succeeded); `PARSE_FAILED`
status is a terminal, visible badge, not an error toast (it's a valid
backend state, not a broken request — see
`state-machines.md#resume-lifecycle`).

---

## `/opportunities` — Opportunities

**Purpose:** the pipeline list — every tracked opportunity, filterable by
`ApplicationStatus`.

**Backend APIs:** `GET /applications?user_id=&status=` (status filter is
optional — omitted for "all").

**Major components:** `StatusFilterTabs` (built from the real
`ApplicationStatus` enum, grouped — see
[user-flows.md#application-tracker-ux](user-flows.md#application-tracker-ux)
for the grouping rationale), `OpportunityTable` (company, title, status
badge, match score, last activity — all fields `ApplicationResponse`
already carries, no extra calls needed for the list view itself).

**User actions:** filter by status; click a row → `/opportunities/:applicationId`.

**Loading:** skeleton table rows.
**Empty:** two distinct empty states — "no opportunities at all" (first
run, same `EmptyState` + CTA to paste a URL) vs. "no opportunities match
this filter" (when a status filter is active) — these must read
differently, since the second one has an obvious fix (clear the filter)
and the first doesn't.
**Error:** page-level error with retry (this is the primary data surface
of the page; a partial/inline error doesn't make sense here the way it
does on the Dashboard).

---

## `/opportunities/:applicationId` — Opportunity Detail

**Purpose:** everything about one opportunity, matching brief section 9's
four panels exactly.

**Backend APIs (in the order the page needs them):**
1. `GET /applications/{application_id}` → primary payload: status, company,
   title, match_score, matched/missing skills, selected_resume_id,
   referral_contact_id, applied_date, notes.
2. `GET /applications/{application_id}/history` → lifecycle timeline.
3. `GET /jobs/{application.job_id}` → full posting detail (company, title,
   processing_status — **see the flagged gap below** for
   location/description/skills).
4. `GET /jobs/{application.job_id}/matches` → `recommendation` (not present
   on `ApplicationResponse`) and `job_match_id`; matched/missing skills and
   score are already available from step 1, so this call is *only* needed
   for the recommendation label and the "other resumes evaluated"
   comparison — **see the flagged gap below**, since that comparison data
   (`profile_scores`) isn't actually exposed by this endpoint today.
5. `GET /profiles/{application.selected_resume_id's profile}` → resume/profile
   summary for the "Selected Resume" panel (skills, title, summary).
6. `GET /jobs/{job_id}/contacts` → ranked contacts panel.
7. `GET /outreach?user_id=` filtered client-side to this `job_id` → outreach
   panel (see the flagged gap in
   [api-mapping.md#outreach-service](api-mapping.md#outreach-service) on
   the missing `job_id` query filter).

**Major components:** `JobInfoPanel`, `MatchPanel` (score, recommendation,
matched/missing skills, selected resume), `OtherResumesEvaluated`,
`ContactsPanel` (reused from the contacts feature), `OutreachPanel`
(read-only summary + "Review" link into `/outreach/:outreachId` if
pending), `LifecycleTimeline`, `StatusActionMenu` (manual
`PATCH /applications/{id}/status`, offering only forward-progress options
per `state-machines.md`).

**✅ RESOLVED (Step 10.5) — Job Information panel:** `JobResponse`
(`src/shared/types/api/jobs.py`) now carries `location`/`description`/
`extracted_skills`/`experience_required`/`source_url`, projected from the
already-persisted `Job` domain fields by `GET /jobs/{job_id}` and
`POST /jobs/ingest-url` alike. The Job Information panel can render the
full set documented in brief section 9 with no workaround needed.

**✅ RESOLVED (Step 10.5) — "other resumes were evaluated":**
`JobMatchResponse` (`src/shared/types/api/matching.py`) now carries
`profile_scores: list[ProfileMatchScore]`, projected from the
already-persisted `JobMatch.profile_scores`. `OtherResumesEvaluated` can
render the full per-profile comparison from `GET /jobs/{job_id}/matches`
with no workaround needed.

**Loading:** the page renders progressively as each call above resolves
independently (no single big spinner blocking the whole page) — see
[async-workflows.md](async-workflows.md).
**Empty:** contacts panel has its own explicit empty state ("No relevant
contacts found for this company yet" — a valid `200 []` result, not an
error, per `api-contracts.md`'s note on
`GET /jobs/{job_id}/contacts`); outreach panel is empty until
`contacts.found` → outreach generation has actually run — shown as
"Outreach hasn't been generated yet" rather than a spinner once the
opportunity's status indicates the automated pipeline has moved past that
point without producing one (see the `CONTACT_SEARCH` no-op case in
`state-machines.md`).
**Error:** `404` on `GET /applications/{id}` is a page-level "not found"
state; failures on the secondary calls (job detail, contacts, outreach) are
scoped to their own panel, not the whole page.

---

## `/outreach` — Outreach Queue

**Purpose:** cross-opportunity inbox for the mandatory human approval step
(brief section 11) — the single place that answers "what needs my
decision right now."

**Backend APIs:**
- `GET /outreach?user_id=&status=PENDING_APPROVAL` (default view)
- `GET /outreach?user_id=` unfiltered (a "History" tab showing
  `APPROVED`/`REJECTED`/`SENT`/`SEND_FAILED`)
- `POST /outreach/{id}/approve`, `POST /outreach/{id}/reject`,
  `POST /outreach/{id}/edit`

**Major components:** `OutreachQueueList`, `OutreachReviewPanel` (draft
message, recipient/contact context, channel badge, approve/edit/reject
actions), `OutreachHistoryTab`.

**User actions:** review a draft → approve (optionally with an edited
final message via the same call — `ApproveOutreachRequest.final_message`),
edit then approve later (`POST /outreach/{id}/edit` sets status `EDITED`,
still requires a separate approve), or reject. **Never** an automatic
"send" action — there is no send endpoint (see
[architecture.md#thin-client-principle](architecture.md#thin-client-principle)).

**Loading:** skeleton list; the review panel shows a spinner only on the
in-flight approve/reject/edit call itself, not on read.
**Empty:** "Nothing waiting for your review" — a genuinely good empty
state, not a dead end, since it means the pipeline is caught up.
**Error:** `409 CONFLICT` on approve/reject (already decided — e.g. two
tabs open) is shown as a specific inline message ("This was already
decided") with an automatic refetch of that item, not a generic error
toast, since it's a real, expected concurrent-edit case documented in
`api-contracts.md`.

---

## `/outreach/:outreachId` — Outreach Review (deep link)

Same `OutreachReviewPanel` component as above, addressable directly (e.g.
from a Dashboard "needs attention" link or a bookmark). `GET /outreach/{id}`
directly rather than filtering the list. No new component — this route
exists purely for deep-linking; it is not a second implementation of the
review UI.

---

## `/settings` — Search Preferences

**Purpose:** `UserPreferences` CRUD (well, RU — no delete concept).

**Backend APIs:** `GET /users/{id}/preferences`, `PUT /users/{id}/preferences`.

**Major components:** `PreferencesForm` (target_roles, target_locations,
remote_preference, excluded_companies, min_salary, salary_currency — all
optional per `UpdateUserPreferencesRequest`).

**User actions:** edit and save (full replace — `PUT`, not `PATCH`; the
form must submit the complete current state each time, matching the
backend contract exactly).

**Loading:** form fields show skeleton placeholders until
`GET /preferences` resolves (or defaults to empty/unset on `404`, which is
a valid response the first time a user hasn't set preferences yet).
**Empty:** N/A — an unset preferences record just means an empty form, not
a distinct empty state.
**Error:** inline field errors on `400 VALIDATION_ERROR`; toast on save
failure with the form left as-is (never silently discard unsaved edits).
