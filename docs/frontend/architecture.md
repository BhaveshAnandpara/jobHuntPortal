# Frontend Architecture

## Stack

| Concern | Choice | Why |
|---|---|---|
| Build tool | Vite | Given. Fast dev server, native ESM, minimal config. |
| Language | TypeScript | Given. Required to consume generated backend types safely. |
| UI library | React 18 | Given. |
| Routing | React Router (`react-router-dom`) | Standard, minimal, data-router APIs (loaders not required — see below) cover nested routes for `/opportunities/:applicationId` and `/outreach/:outreachId` cleanly. |
| Server state | TanStack Query (`@tanstack/react-query`) | The backend is entirely REST + polling-friendly (see [async-workflows.md](async-workflows.md)); Query's caching, `refetchInterval`, and mutation/invalidation model map directly onto "render state → accept action → call backend → render updated state" without hand-rolled state. |
| Forms | React Hook Form + Zod | Two genuinely form-heavy surfaces exist (Preferences, Outreach edit) plus resume upload and job-URL submission; RHF avoids re-render-per-keystroke, Zod schemas double as the base for lightweight client-side validation mirroring backend `VALIDATION_ERROR` rules. Not used for read-only pages — no form library overhead where there is no form. |
| Styling / components | Tailwind CSS + a small set of Radix UI primitives (Dialog, Tabs, Select, Toast portal) | Utility CSS gives the "clarity, whitespace, information hierarchy" direction in [design-system.md](design-system.md) without hand-writing a component library; Radix supplies accessible primitives (focus trap, keyboard nav, ARIA) only where genuinely needed (modals, dropdowns), not a full opinionated design system like MUI/Chakra which would fight the "modern productivity tool, not an AI demo" direction and add real bundle weight. |
| Notifications | `sonner` | Single-purpose, ~2KB, accessible toast primitive — used for mutation success/failure feedback (approve, reject, upload). No need to hand-build toast state. |
| Icons | `lucide-react` | Tree-shakeable, consistent stroke-based icon set, no themed/gradient/"AI sparkle" iconography. |
| API types | Generated from the FastAPI OpenAPI schema via `openapi-typescript` (dev dependency, build-time only, zero runtime) | See [state-management.md#type-strategy](state-management.md#type-strategy). |
| Testing | Vitest + React Testing Library + MSW + Playwright | See [testing-strategy.md](testing-strategy.md). |

**Deliberately not used:** Redux/Redux Toolkit (no cross-cutting client
state large enough to justify it — see
[state-management.md](state-management.md)), a full component library
(MUI/Chakra/Ant — see above), Axios (native `fetch` wrapped in one small
client is sufficient), GraphQL (backend is REST-only), WebSockets/SSE (not
in backend scope this step — see [async-workflows.md](async-workflows.md)).

## Thin-client principle

```
User Action
     │
     ▼
React Component (renders current state, captures intent)
     │
     ▼
api/*.ts  (typed fetch wrapper — no logic, just a call)
     │
     ▼
FastAPI endpoint (owns validation, business rules, persistence, events)
     │
     ▼
TanStack Query cache invalidation / refetch
     │
     ▼
React re-renders from the new server state
```

The frontend never independently decides: which resume is the best fit,
whether a job is worth pursuing, who a good contact is, what an outreach
message should say, whether a status transition is valid, or whether
outreach is allowed to send. Every one of those decisions is made by
LangGraph/backend logic already implemented and tested. Concretely, this
means:

- No score/ranking computation in React — scores are rendered as received.
- No local re-implementation of `state-machines.md`'s transition rules —
  the status-change UI (`PATCH /applications/{id}/status`) offers only the
  actions the backend's own validation will accept, but the *authority* for
  what's valid is the `400 VALIDATION_ERROR` response, not a duplicated
  rules table in the frontend. (The dropdown of "next status" options may
  be pre-filtered as a UX nicety using the same enum values already public
  in `ApplicationStatus`, but this is a convenience, not enforcement.)
- No client-side outreach approval bypass — approval is exactly one API
  call (`POST /outreach/{id}/approve`); there is no "send" action anywhere
  in the frontend, because there is no send endpoint — sending is a
  Kafka-triggered backend side effect of approval (see
  [state-machines.md#outreach-lifecycle](../architecture/state-machines.md#outreach-lifecycle-outreachstatus--outreachstatus)).

## High-level shape

```
                         Browser (React SPA)
   ┌──────────────────────────────────────────────────────────┐
   │  app/        routing, layout, providers                  │
   │  features/*  one folder per product area                 │
   │  api/*       typed fetch wrapper, one module per service │
   │  components/ shared, business-agnostic UI primitives     │
   └──────────────────────────────────────────────────────────┘
                              │  HTTPS / JSON, polling via TanStack Query
                              ▼
                    FastAPI (src/api/main.py)
       ┌───────┬──────────┬──────────┬──────────┬──────────┐
       │ users │ profiles │ jobs     │ matching │ contacts │ ...
       └───────┴──────────┴──────────┴──────────┴──────────┘
                              │
                    Kafka + LangGraph + PostgreSQL
                    (unchanged — already validated in Step 9)
```

There is exactly one new network boundary: browser → FastAPI over HTTP.
Everything to the right of FastAPI is the already-validated backend and is
out of scope for this frontend effort.

## No authentication layer yet

Confirmed via `src/shared/types/api/users.py` and `dependency-graph.md`:
the backend has no login/session/token endpoint — `POST /users` is a bare
create, and every other endpoint takes `user_id` as a plain path/query
parameter with no auth header. This is not a frontend decision to make; see
[api-mapping.md#backend-gaps-affecting-this-mapping](api-mapping.md#backend-gaps-affecting-this-mapping)
for how the frontend works within that constraint for MVP scope, and why it
is not a blocker for a single-user local deployment (matches
`about_project.md`'s stated initial goal: "capable of running locally with
minimal cost").
