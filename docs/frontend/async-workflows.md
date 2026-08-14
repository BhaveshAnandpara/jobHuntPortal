# Asynchronous Workflow UX

## Mechanism: TanStack Query polling only (no WebSockets/SSE)

Per the Step 10 brief's explicit instruction not to add backend
WebSocket/SSE infrastructure this step, and per
[architecture.md](architecture.md)'s stack choice, all "the backend is
still working on this" UX uses `useQuery`'s `refetchInterval`, scoped
narrowly so the app isn't polling everything constantly:

| Where | What's polled | Interval | Stop condition |
|---|---|---|---|
| `/resumes` | `GET /resumes?user_id=` | 2s | every resume's `status` is a terminal value (`PARSED` or `PARSE_FAILED`) — poll stops entirely, not per-row |
| `/opportunities` (list) | `GET /applications?user_id=&status=` | 5s | never fully stops (new opportunities can appear at any time from automated discovery or a new URL submission) — but only while the tab is visible, via `refetchIntervalInBackground: false` and TanStack Query's default `refetchOnWindowFocus` |
| `/opportunities/:applicationId` | `GET /applications/{id}` | 3s | `status` reaches a terminal state (`OFFER`, `REJECTED`, `IGNORED`, `WITHDRAWN`) **or** the automated chain's last unattended step (`OUTREACH_SENT`) — beyond that every remaining transition (`REFERRED`→`APPLIED`→`INTERVIEW`→...) is manual, so there's nothing left to poll for until the user acts |
| `/outreach` (queue) | `GET /outreach?user_id=&status=PENDING_APPROVAL` | 5s | never fully stops (same reasoning as the opportunities list) |
| Dashboard | `GET /applications`, `GET /outreach?status=PENDING_APPROVAL` | 10s | never fully stops, but the slowest interval of any page since it's a summary view, not the primary place a user watches active progress |

Every other query on every other page (contacts, one outreach item, job
detail, match, profiles, preferences, history) is fetch-once +
`refetchOnWindowFocus` — no interval. These represent data that only
changes in reaction to a user action elsewhere (approving outreach,
editing preferences) or a one-time backend computation that has either
finished or hasn't started, not an in-progress process worth polling.

## Resume parsing (progressive disclosure)

```
POST /resumes  →  ResumeResponse { status: UPLOADED }
       │
       ▼
Resume row appears immediately, status badge "Uploaded"
       │
       ▼  (2s poll of GET /resumes)
status: PARSING  →  badge "Parsing..." (with a subtle progress indicator,
       │             not a determinate percentage — the backend doesn't
       │             expose one)
   ┌───┴────┐
   ▼        ▼
PARSED   PARSE_FAILED
(terminal)  (terminal)
   │        │
   ▼        ▼
badge     badge "Parse failed" + the profile-summary section for this
"Parsed",  row stays absent (there is no CandidateProfile for a failed
profile    parse) — the row itself is not removed, so the user still sees
summary    what they tried to upload and when
now shown
```

## Full pipeline progression on Opportunity Detail (no page reload)

```
GET /applications/{id} polled every 3s while status is non-terminal
       │
       ▼
Each poll's response re-renders whichever panels that status unlocks:

DISCOVERED          → Job Info panel only (Match/Contacts/Outreach panels
                       show "Not yet analyzed" placeholders, not spinners —
                       spinners imply "loading now", these are "hasn't
                       started yet")
MATCHED/SHORTLISTED → Match panel populates
CONTACT_SEARCH       → Contacts panel shows a lightweight in-progress
                       indicator
CONTACT_FOUND         → Contacts panel populates (or its own real empty
                       state if the search found nothing)
OUTREACH_GENERATED   → Outreach panel populates with the draft, "Review"
                       CTA appears
OUTREACH_APPROVED/SENT → Outreach panel updates status badge, Timeline
                       gets a new entry
```

This is a single component tree re-rendering from one polled query, not a
page navigation or reload — React's normal re-render on new query data is
sufficient; no manual DOM patching or route change is needed to move
"through" these states.

## Explicit non-goals for this step

No WebSocket/SSE server push, no service worker, no background sync. If
polling proves too coarse later (e.g. real users want sub-second
outreach-approval-to-send visibility), that is a backend-plus-frontend
change requiring a new architecture decision — not something to route
around client-side with a shorter and shorter polling interval, which
would just add backend load without fixing the underlying latency.
