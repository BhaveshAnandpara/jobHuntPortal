# User Flows

## First-visit flow

```
First Visit (no identity in localStorage)
       │
       ▼
  /welcome — Create Identity (POST /users)
       │
       ▼
  Identity persisted to localStorage
       │
       ▼
  / (Dashboard) — empty-state: "Upload a resume to get started"
       │
       ▼
  /resumes — Upload Resume(s) (POST /resumes, one call per file)
       │
       ▼
  Per-resume: PARSING → PARSED (or PARSE_FAILED), polled — see
  async-workflows.md#resume-parsing
       │
       ▼
  / (Dashboard) — now shows the primary action prominently
```

Preferences (`/settings`) are **not** forced during onboarding — brief
section 2 lists "define job-search preferences" as a capability, not a
gate, and `UserPreferences` is optional input to matching
(`UserPreferencesResponse`'s fields are all nullable/empty-default). Making
it mandatory would block the primary "paste a URL and see what happens"
path for no backend-required reason.

## Job submission flow

```
Paste Job URL (Dashboard or Opportunities page — same component)
       │
       ▼
  Client-side validation: non-empty, well-formed URL (cheap UX check only —
  the backend's INVALID_JOB_URL is still the real authority)
       │
       ▼
  POST /jobs/ingest-url  { user_id, url }
       │
   ┌───┴────────────────────────┐
   ▼                            ▼
202 Accepted                 400 / INVALID_JOB_URL or VALIDATION_ERROR
JobResponse returned          │
   │                          ▼
   ▼                     Inline error under the input,
Navigate toward the       form stays populated for correction
opportunity's detail
page — see note below
```

**Note on "duplicate-job behavior":** the backend's job-URL ingestion is
already idempotent per user (`Job.` uniqueness on `user_id` + URL,
confirmed via the Step 9 integration report's idempotency validation) —
`POST /jobs/ingest-url` transparently returns/reuses the existing job
rather than erroring. The frontend does not need to detect or handle
duplicates itself: it always just navigates toward the resulting job's
opportunity, whether that job is brand new or was already known. This is a
concrete instance of the thin-client principle — the dedup *decision*
already happened in the backend before the response came back.

**Note on "success navigation":** `POST /jobs/ingest-url` returns a
`JobResponse` (has `job_id`), not an `ApplicationResponse` (has
`application_id`, the actual detail-route key — see
[routes.md](routes.md#why-this-differs-from-the-example-route-list-in-the-step-10-brief)).
Tracking Service creates the `Application` asynchronously, in reaction to
`jobs.discovered` — not synchronously inside the ingest call. So the
frontend cannot navigate straight to `/opportunities/:applicationId` the
instant `POST /jobs/ingest-url` returns; the `Application` row may not
exist for a brief moment yet. Correct handling: navigate to
`/opportunities` (the list) with the new job's `company`/`title` optimistically
highlighted, and let `GET /applications` polling (see
[async-workflows.md](async-workflows.md)) surface the new row and its
status as it appears — not a blocking wait screen. This is a real,
non-obvious timing detail worth calling out explicitly so the
opportunities-agent doesn't build a synchronous-looking transition where
none exists on the backend.

## Full opportunity lifecycle (what the user sees at each stage)

```
Job Submitted
      │  Application not visible yet (async creation, see note above)
      ▼
DISCOVERED         "Analyzing..." — Opportunities row shows a processing badge
      │
      ▼
MATCHED             Match score + selected resume now visible
      │
   ┌──┴───┐
   ▼      ▼
IGNORED  SHORTLISTED   "Ignored" opportunities still listed (filterable),
(terminal) │            not hidden — the user should be able to see what
           ▼            the system passed on and why (matched/missing skills)
     CONTACT_SEARCH     "Finding relevant contacts..."
           │
           ▼
     CONTACT_FOUND      Ranked contacts panel populated (or explicit
           │             "no contacts found" empty state)
           ▼
 OUTREACH_GENERATED     Draft appears in /outreach queue — badge/count on
           │             Dashboard "needs attention" and on the nav item
           ▼
     [ user decides: approve / edit+approve / reject ]
           │
   ┌───────┼────────┐
   ▼       ▼        ▼
APPROVED REJECTED  (edited then approved)
   │    (terminal)
   ▼
OUTREACH_SENT        Timeline shows the sent message; SEND_FAILED is a
   │                  distinct, visible terminal badge if the provider call
   ▼                  failed (not silently retried forever — see
REFERRED              state-machines.md's outreach lifecycle)
   │  manual (user confirms a referral occurred)
   ▼
APPLIED → INTERVIEW → OFFER / REJECTED
   (all manual, via the Status Action Menu on Opportunity Detail)
```

Every state above is rendered from `ApplicationStatus` values the backend
already returns — no frontend-invented intermediate states (per brief
section 12's explicit instruction). "Analyzing..." / "Finding relevant
contacts..." etc. are just human-readable *labels* mapped 1:1 from the
existing enum values, not new states.

## Multiple-resumes UX

```
Resume Management (/resumes)
      │
      ├── Upload Resume A → PARSING → PARSED → CandidateProfile A (ACTIVE)
      ├── Upload Resume B → PARSING → PARSED → CandidateProfile B (ACTIVE)
      └── Upload Resume C → PARSING → PARSE_FAILED (terminal, visible badge)
                                          │
                                          ▼
                              User uploads a corrected file as
                              a NEW resume (not a retry of C —
                              matches state-machines.md exactly)
```

On a specific Opportunity Detail page:

```
Selected Resume: "Java Backend Resume" (from ApplicationResponse.selected_resume_id)
      │
      ▼
Why selected → matched_skills / missing_skills (already on ApplicationResponse)
      │
      ▼
Match score → 92% (already on ApplicationResponse)
      │
      ▼
Other resumes evaluated → GET /jobs/{job_id}/matches's profile_scores
(resolved Step 10.5 — see api-mapping.md's backend-gaps note), rendered as
received; never computed client-side (that would violate the thin-client
principle even where the data happens to be simple to re-derive).
```

## Contact discovery UX

Ranked contacts render exactly the fields `ContactResponse` provides —
`full_name`, `headline`, `company`, `contact_type` (rendered as a
human-readable label, e.g. `HIRING_MANAGER` → "Hiring Manager" — a pure
label mapping, not new logic), `profile_url`, `relevance_score`. The score
is shown as a simple visual indicator (e.g. a 0–10 bar or badge) — the
frontend does not explain *how* the score was computed (no ranking-signal
breakdown is exposed by `ContactResponse`, and inventing one would be
speculative). Empty state: "No relevant contacts found for this company
yet" — a valid `200 []`, not an error (see `api-contracts.md`'s explicit
note that Contact Discovery Service cannot distinguish "no contacts" from
"unknown job" and intentionally returns `200` either way).

## Outreach review UX

```
Outreach Queue (/outreach)
      │
      ▼
Select a pending item
      │
      ▼
Read: draft_message, channel, recipient context (contact_id → resolved via
      the contacts already fetched for that job, or a lightweight
      per-contact lookup if reached directly via deep link)
      │
   ┌──┴───────────────┬──────────────────┐
   ▼                   ▼                  ▼
Approve as-is      Edit then decide    Reject
POST .../approve   POST .../edit       POST .../reject
{final_message:    → status EDITED     → status REJECTED (terminal)
 null}             → then approve or
   │               reject separately        │
   ▼                   │                    ▼
status APPROVED        ▼                REJECTED, sent_at stays null,
sent_at still null  (same two branches   removed from the pending queue
(send is async,      as above)
backend-driven —
see architecture.md)
```

The UI must never imply "Approve" = "Sent". `OutreachStatus.APPROVED` and
`OutreachStatus.SENT` are rendered as visually distinct badges (see
[design-system.md](design-system.md#status-badges)), and the review
panel's post-approve confirmation copy says "Approved — will be sent
shortly" rather than "Sent", matching brief section 11's explicit
"Generated ≠ Sent" requirement.

## Application tracker UX

The `/opportunities` list groups the 15 `ApplicationStatus` values into a
small set of filter tabs, since 15 raw tabs would be unusable:

| Tab | Statuses included |
|---|---|
| Active | `DISCOVERED`, `MATCHED`, `SHORTLISTED`, `CONTACT_SEARCH`, `CONTACT_FOUND`, `OUTREACH_GENERATED`, `OUTREACH_APPROVED`, `OUTREACH_SENT`, `REFERRED` |
| Applied | `APPLIED`, `INTERVIEW` |
| Closed | `OFFER`, `REJECTED`, `IGNORED`, `WITHDRAWN` |
| All | (no filter) |

This grouping is a pure display convenience over the real enum, not a new
backend concept. `GET /applications?status=` only accepts a single exact
`ApplicationStatus` value, and there is no "status in [...]" variant — so a
group tab (Active/Applied/Closed) fetches unfiltered
(`GET /applications?user_id=`, no `status` param) and partitions the
result client-side by that tab's status set. A future, more granular
per-status filter (a dropdown listing all 15 values individually) can use
the single-`status` query param directly with no partitioning needed. Both
approaches read from the same one contract; nothing here asks for a
backend change.
