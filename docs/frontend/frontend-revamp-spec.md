# Frontend Revamp Spec

Status: Draft — ready for ticket pickup
Supersedes: `api-mapping.md` and `architecture.md` wherever they conflict with this document (see "Why this doc exists" below)

## 1. Overview

This document is the spec for revamping the JobHunt frontend (`d:\JobHunt\frontend`). It has two parts:

1. **A corrected baseline reference** (Section 2) — the actual routes, flows, and API calls as they exist in the codebase today. The previous docs in this folder (`api-mapping.md`, `architecture.md`, `routes.md`) were written before a JWT auth layer was added to the backend and no longer match the real code in several places. Section 2 replaces those sections rather than trusting the prose in the older files.
2. **A ticket/todo breakdown for the redesign** (Section 4) — migrating the component layer to [shadcn/ui](https://ui.shadcn.com/) and applying the `anti-ui-slop` design methodology (see Section 3) across every page, equally. No visual/brand direction (colors, mood, reference products) was supplied for this round, so tickets start from shadcn's default neutral theme; that can be swapped later without re-doing the ticket breakdown.

**Explicitly out of scope for this round:** backend changes, and cleanup of known code-quality gaps (a stray `console.log` in `DashboardPage.tsx`, missing per-resource ownership checks on several backend endpoints, no `job_id` filter on `GET /outreach`). These are noted inline where relevant but do not have their own tickets.

### Why this doc exists (the drift that was found)

The frontend is **not** greenfield — it's a fully built, tested (295 passing unit tests, 9 Playwright e2e specs), deployed React 19 + Vite + TanStack Query + Tailwind 4 + Radix SPA. Every route below is implemented. But the backend gained a real auth layer since the older docs were written:

- Login is now `POST /auth/login`, and `POST /users` auto-logs-in and returns a JWT (`LoginResponse{access_token, token_type, user}`).
- Most endpoints that used to take `?user_id=` now derive identity from the `Authorization: Bearer <token>` header instead. For example, preferences are `GET/PUT /users/me/preferences`, not `GET/PUT /users/{user_id}/preferences`.
- The frontend already reflects this correctly (`src/hooks/identity.ts`, `src/api/client.ts` attaches the bearer token, global 401 handler redirects to `/login`) — it's the docs that lagged, not the code.

Any redesign ticket that touches auth-adjacent screens (login, register, any 401 handling) should follow the code in `src/api/client.ts` and `src/hooks/IdentityProvider.tsx`, not the old `api-mapping.md`.

## 2. Corrected Baseline Reference

### 2.1 Routes (from `src/app/router.tsx`)

All 9 routes are implemented. `/login` and `/register` are public; every other route sits behind `<RequireIdentity>` (redirects to `/login` if no token) inside a shared `<Layout>`, and every route is individually wrapped in `<ErrorBoundary>`.

| Route | Page component | Purpose |
|---|---|---|
| `/login` | `LoginPage` | Sign in, get JWT |
| `/register` | `RegisterPage` | Create account, auto-login |
| `/` | `DashboardPage` | Primary action (submit job URL) + pipeline summary + needs-attention list |
| `/resumes` | `ResumesPage` | Upload/list/delete resumes, view derived profiles |
| `/opportunities` | `OpportunitiesPage` | Full pipeline list, filterable by status (this *is* the application tracker) |
| `/opportunities/:applicationId` | `OpportunityDetailPage` | Job info, match, contacts, outreach, lifecycle history for one opportunity |
| `/outreach` | `OutreachQueuePage` | Cross-opportunity queue of outreach needing a decision, plus history |
| `/outreach/:outreachId` | `OutreachReviewPage` | Deep-linkable approve/edit/reject panel for one outreach item |
| `/settings` | `SettingsPage` | Search preferences only (no general profile editing) |

### 2.2 Auth model

- JWT bearer token, obtained via `POST /auth/login` (`LoginRequest{email, password}` → `LoginResponse`) or automatically via `POST /users` (`CreateUserRequest{email, display_name, password, timezone?}` → `LoginResponse`, 201).
- Token stored in `localStorage` (`jobhunt.token`); attached as `Authorization: Bearer <token>` on every request by `src/api/client.ts`.
- Global 401 handler clears the token and hard-redirects to `/login` (except on the login call itself, so invalid-credentials can be shown inline).
- `userId` is decoded client-side from the JWT for display/cache-key purposes only — not a security boundary.
- On login/logout, `queryClient.clear()` runs to prevent cross-account cache bleed.

### 2.3 Per-page flow and API calls (verified against `src/api/*.ts` and backend route files)

**`/login`, `/register`**
- `POST /auth/login`, `POST /users`

**`/` Dashboard**
- `POST /jobs/ingest-url` — primary action, submit a job posting URL (202, publishes `jobs.discovered` server-side; the new Application row appears asynchronously, so submitting navigates to `/opportunities`, not directly to a detail page)
- `GET /applications[?status]` — pipeline summary + recent opportunities (polled every 10s)
- `GET /outreach?status=PENDING_APPROVAL` — needs-attention banner (polled every 10s)

**`/resumes`**
- `GET /resumes`, `POST /resumes` (body is base64-encoded `file_content`, not multipart/form-data — a real wire-format detail, not a choice to revisit casually), `DELETE /resumes/{resume_id}`
- `GET /profiles` — derived candidate profiles
- Polled every 2s while any resume is non-terminal (`UPLOADED`/`PARSING`), stops once all are `PARSED`/`PARSE_FAILED`

**`/opportunities`**
- `GET /applications[?status]` — polled every 5s

**`/opportunities/:applicationId`**
- `GET /applications/{id}` (polled every 3s until a terminal/near-terminal status)
- `GET /applications/{id}/history`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/matches`
- `GET /profiles/{profile_id}`
- `GET /jobs/{job_id}/contacts`
- `GET /outreach[?status]`, filtered client-side by `job_id` (backend has no `job_id` query filter on this endpoint — known non-blocking gap, not in scope to fix here)

**`/outreach`, `/outreach/:outreachId`**
- `GET /outreach?status=PENDING_APPROVAL` (default queue view, polled every 5s) + unfiltered history tab
- `GET /outreach/{outreach_id}`
- `POST /outreach/{outreach_id}/approve` (`ApproveOutreachRequest{final_message?}`) — **the only path that triggers an actual send**, via a backend Kafka consumer reacting to `outreach.approved`; approving is not the same event as sending
- `POST /outreach/{outreach_id}/reject`
- `POST /outreach/{outreach_id}/edit` (`EditOutreachRequest{message}`, sets status `EDITED`)
- All three actions can return `409` if the item was already decided (e.g. double-click, or decided elsewhere) — must be handled, not retried blindly

**`/settings`**
- `GET /users/me/preferences`, `PUT /users/me/preferences` (`UpdateUserPreferencesRequest{...}` — full replace, not a patch)

### 2.4 State management (still accurate, carry forward as-is)

- **Server state**: TanStack Query exclusively. Query keys centralized in `src/api/queryKeys.ts` — no ad hoc inline key arrays. Mutations invalidate only the specific keys they affect.
- **Auth/identity state**: one `IdentityContext`/`IdentityProvider` (React Context) holding token/userId, backed by `localStorage`. This is the only durable client-side state — there is no Redux/Zustand/MobX.
- **Forms**: react-hook-form + zod.
- **Async pattern**: polling only, via `hooks/usePolling.ts`, at the per-page intervals listed in 2.3. No WebSockets/SSE (explicit, documented non-goal — not something to "fix" as part of this redesign).

### 2.5 Error contract

Every 4xx response is `{"detail": {"code": "<ErrorCode>", "message": "<str>"}}`, normalized once in `src/api/client.ts` into a single `ApiError` shape (plus a frontend-only `NETWORK_ERROR` sentinel for an unreachable server). Presentation conventions (toast for mutation failure, page-level error+retry for a failed primary query, inline panel-scoped error for a failed secondary panel, persistent banner for network failure) are documented in `error-handling.md` and remain accurate — redesign tickets should restyle these, not redesign the decision logic.

### 2.6 Invariant that must survive the redesign

Outreach sending is gated entirely behind human approval (`POST /outreach/{id}/approve`) — this is enforced by the backend's state machine and covered by a dedicated integration test suite, not just a UI convention. Every redesign ticket touching outreach UI must keep **Approved** and **Sent** visually and textually distinct (e.g. "Approved — will be sent shortly" is not the same state as "Sent").

## 3. Redesign Principles

Applying the `anti-ui-slop` methodology (`.agents/skills/anti-ui-slop/SKILL.md`) plus a shadcn/ui component migration:

1. **No filler.** Use only real, product-specific labels and data already available from the API responses in Section 2.3. Never invent metrics, activity feeds, testimonials, or controls that don't map to a real backend capability.
2. **Full state coverage per screen.** Every page/panel must explicitly design for: loading, empty, error, success (post-action confirmation), disabled, and permission/auth states — mapped to this app's real cases (e.g. "no resumes yet" empty state on `/resumes`, "contact search returned zero results", a `409` "already decided" state on outreach actions, `PARSE_FAILED` on a resume row).
3. **One clear primary action per screen** — carried forward from the existing `design-system.md` direction (e.g. "Analyze Job" on Dashboard, "Approve" in outreach review); this still holds and should not be diluted by the redesign.
4. **shadcn/ui as the component foundation.** Install via the shadcn CLI (`components.json`, Tailwind theme tokens, `cn()` utility, `class-variance-authority`) and use its primitives — button, card, dialog, select, tabs, table, input, textarea, badge, skeleton, sonner/toast, form — in place of the current hand-rolled wrappers in `src/components/` (`Button`, `Card`, `StatusBadge`, `EmptyState`, `ErrorState`, `Spinner`, `Skeleton`, `PageHeader`, `Input`, `Textarea`, `FieldError`, `Select`, `Dialog`, `Toaster`, `Table`). Preserve the existing *props/usage contracts* consumed by feature code where practical, so migrating a primitive doesn't force a rewrite of every feature file that uses it.
5. **Respect existing, still-valid conventions**: the five status-category color mapping (in-progress/needs-attention/positive/negative/neutral, not per-enum-value colors), desktop-first responsive behavior (table→stacked-card collapse below ~768px), and WCAG AA contrast — these are documented in `design-system.md` and should be re-implemented with shadcn tokens, not abandoned.

## 4. Tickets

Each ticket lists Goal, relevant API calls (from Section 2.3, unchanged by this redesign), design requirements, and acceptance criteria.

---

**T1 — shadcn/ui foundation setup**
- Goal: Establish shadcn/ui as the component foundation without breaking existing pages.
- API calls: none (tooling only).
- Design requirements: Run shadcn CLI init; add `components.json`; wire Tailwind 4 theme tokens (map existing `@theme` brand + status colors from `src/index.css` into shadcn's CSS variables rather than discarding them); add `cn()` utility and `class-variance-authority`/`tailwind-merge`.
- Acceptance criteria: CLI-added components build and render in isolation; existing app still builds and all current tests pass unmodified.

**T2 — Migrate core primitives to shadcn**
- Goal: Replace `src/components/{Button,Input,Textarea,Select,Dialog,Card,Skeleton,Table,StatusBadge,Toaster}` internals with shadcn equivalents (button, input, textarea, select, dialog, card, skeleton, table, badge, sonner).
- API calls: none.
- Design requirements: Keep each component's existing exported prop contract (per `src/components/index.ts`) so feature files don't need to change; `StatusBadge` keeps the category-based color mapping from `design-system.md`, re-implemented with shadcn `badge` variants.
- Acceptance criteria: All existing component `.test.tsx` files pass against the new internals with no or minimal prop-level changes; visual smoke check of each primitive.

**T3 — Redesign global shell**
- Goal: Restyle `Layout`, navigation, `ErrorBoundary` fallback, and the `RequireIdentity` redirect experience.
- API calls: none (uses auth state only).
- Design requirements: Cover the "session expired / redirected to login" state explicitly (not just a silent redirect); error boundary fallback needs a real recovery action, not a dead end.
- Acceptance criteria: Manual walk of login → app → force a 401 → confirm redirect UX; error boundary triggers on a thrown error and offers reload.

**T4 — Redesign Login / Register**
- Goal: Restyle `LoginPage`/`RegisterPage` with shadcn `form`/`input`/`button`.
- API calls: `POST /auth/login`, `POST /users`.
- Design requirements: Cover loading (submitting), error (401 invalid credentials, validation errors), and success (redirect) states; inline field-level errors on validation failure, not just a toast.
- Acceptance criteria: Wrong password shows inline error without clearing the form; successful login redirects to `/`.

**T5 — Redesign Dashboard (`/`)**
- Goal: Restyle the job-URL submit form, pipeline summary cards, and needs-attention list.
- API calls: `POST /jobs/ingest-url`, `GET /applications[?status]`, `GET /outreach?status=PENDING_APPROVAL`.
- Design requirements: Empty state for a brand-new account (no resumes yet → should point at `/resumes`, not just show zeros); loading skeletons for summary cards; inline error under the URL input for `INVALID_JOB_URL` (400); remove the existing stray `console.log` debug statement in `DashboardPage.tsx` while touching this file (incidental cleanup, not a separate ticket).
- Acceptance criteria: Submitting a URL shows a clear success path to `/opportunities`; summary counts match `GET /applications` data; needs-attention list links directly to `/outreach/:id`.

**T6 — Redesign Resumes page**
- Goal: Restyle upload/list/delete flow and profile summaries.
- API calls: `GET/POST /resumes`, `DELETE /resumes/{id}`, `GET /profiles`.
- Design requirements: Progressive states per resume row (`UPLOADED → PARSING → PARSED | PARSE_FAILED`); empty state for zero resumes; delete confirmation; profile summary only renders once `PARSED`.
- Acceptance criteria: Uploading shows an indeterminate progress badge that resolves to a terminal state without a page reload; failed parse is visibly distinct and doesn't block other rows.

**T7 — Redesign Opportunities list**
- Goal: Restyle the status-filterable pipeline table.
- API calls: `GET /applications[?status]`.
- Design requirements: Two distinct empty states (no opportunities at all vs. none matching the current filter tab); table collapses to stacked cards below ~768px.
- Acceptance criteria: Switching filter tabs updates the list without a full-page loading flash; mobile viewport shows cards, not a squeezed table.

**T8 — Redesign Opportunity Detail**
- Goal: Restyle the multi-panel detail page: Job Info, Match, Other Resumes Evaluated, Contacts, Outreach Summary, Lifecycle Timeline, Status Action Menu.
- API calls: `GET /applications/{id}`, `GET /applications/{id}/history`, `GET /jobs/{job_id}`, `GET /jobs/{job_id}/matches`, `GET /profiles/{profile_id}`, `GET /jobs/{job_id}/contacts`, `GET /outreach[?status]` (client-filtered).
- Design requirements: Each panel unlocks progressively as the opportunity's status advances (e.g. Contacts panel shows "not yet searched" placeholder pre-`CONTACT_SEARCH`, not a spinner); 2-column desktop / stacked mobile layout; each panel has its own inline error+retry independent of the others; `409` handling on any status-change action.
- Acceptance criteria: Loading the page for an opportunity at each major lifecycle stage renders the correct set of populated vs. placeholder panels; a failed secondary panel (e.g. contacts) doesn't block the rest of the page.

**T9 — Redesign Outreach queue + review**
- Goal: Restyle the queue list and the approve/edit/reject review panel (both the standalone page and the queue's inline panel).
- API calls: `GET /outreach?status=PENDING_APPROVAL`, `GET /outreach/{id}`, `POST /outreach/{id}/approve|reject|edit`.
- Design requirements: Approve/Reject/Edit as clearly distinct actions with one primary (Approve); explicit "Approved — will be sent shortly" vs. "Sent" distinction (never conflate, per Section 2.6); `409` state shows "already decided" inline and refetches rather than retrying the action.
- Acceptance criteria: Editing a draft updates status to `EDITED` and reflects in the queue; approving a second time after another tab already approved shows the 409 state, not a silent failure.

**T10 — Redesign Settings / Preferences**
- Goal: Restyle the preferences form.
- API calls: `GET/PUT /users/me/preferences`.
- Design requirements: Full-replace semantics should be visually clear (e.g. no partial-save affordance); validation errors inline per field; save confirmation via toast.
- Acceptance criteria: Saving with invalid data shows field-level errors without losing other entered values; successful save is confirmed and reflected on reload.

**T11 — Cross-page anti-ui-slop finish gate**
- Goal: Final pass across all 9 routes after T3–T10 land.
- API calls: none (verification only).
- Design requirements: Render every page and confirm all required states (Section 3, item 2) are actually reachable and correctly styled; check responsive collapse behavior at the documented breakpoint; confirm no placeholder/filler content slipped in; confirm the five status-category colors are used consistently via `StatusBadge` everywhere (no one-off color usage).
- Acceptance criteria: A written checklist (one row per route × required state) with every cell checked off; existing Playwright e2e suite (`tests/e2e/*`) still passes.
