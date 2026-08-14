# Frontend Agent Ownership

## Why 8 agents, not the 10 suggested in the brief

The brief's section 21 suggested `frontend-shell-agent`,
`frontend-api-agent`, `frontend-profile-agent`, `frontend-jobs-agent`,
`frontend-contacts-agent`, `frontend-outreach-agent`,
`frontend-tracking-agent`, `frontend-design-agent`, `integration-ui-agent`
— 9 named plus itself asked "determine whether all of these are actually
necessary."

**Dropped: `frontend-tracking-agent`.** As established in
[routes.md](routes.md) and
[repository-structure.md](repository-structure.md#why-featurestracking-doesnt-exist-as-its-own-folder),
the backend has one list/detail data surface (`GET /applications`, `GET
/applications/{id}`, `GET /applications/{id}/history`) for both "jobs
pipeline" and "application tracker" — the brief's example treated these as
two features because its example route list (`/jobs` + `/applications`)
assumed two backend surfaces that don't actually exist. A dedicated
tracking agent would necessarily edit the same components
(`OpportunityDetail`, the status timeline) that `frontend-jobs-agent`
(renamed `frontend-opportunities-agent` here, since "jobs" alone
undersells that it also owns application status/lifecycle) already owns —
exactly the "heavily overlapping ownership" the brief said to avoid.
Merged into one agent instead.

**Kept, but scoped narrowly: `frontend-contacts-agent`.** Its owned surface
(one panel, one empty state, one score display) is small, but it's still
kept separate because it's genuinely developable and testable in isolation
against `GET /jobs/{job_id}/contacts` alone, and
`frontend-opportunities-agent`'s detail page only needs to import
`ContactsPanel` as a already-built black box — this is a real parallelism
win, not busywork.

**Everything else kept as suggested**, with `frontend-jobs-agent` renamed
`frontend-opportunities-agent` to match the actual IA.

## Build order

Three agents have no dependency on any other frontend code and should
start first, in parallel:

```
frontend-shell-agent  ─┐
frontend-design-agent  ─┼──►  (everyone else)
frontend-api-agent     ─┘
```

Once those three land, the five feature agents run in parallel (each
depends on `api/`, `components/`, and `app/` existing, but not on each
other):

```
frontend-profile-agent
frontend-opportunities-agent    all parallel, zero cross-dependencies
frontend-contacts-agent
frontend-outreach-agent
```

`integration-ui-agent` runs last, after all feature agents land — its job
is cross-feature wiring (e.g. the Dashboard's "needs attention" list
linking into `frontend-outreach-agent`'s queue) and the Playwright suite,
neither of which can be meaningfully done before the features they wire
together exist.

---

## `frontend-shell-agent`

**OWNS:** `src/app/**`, `index.html`, `vite.config.ts`, `tsconfig.json`,
`package.json` (initial scaffold + dependency list from
[architecture.md](architecture.md)'s stack table).

**INPUTS:** the route table in [routes.md](routes.md); the provider list
in [state-management.md](state-management.md) (`QueryClientProvider`,
`IdentityContext`).

**OUTPUTS:** a running Vite dev server with an empty-but-routed app shell —
every route in [routes.md](routes.md) resolves to a placeholder page,
navigation/layout/nav bar works, `IdentityContext` correctly redirects
`/` → `/welcome` when no identity is persisted.

**DEPENDENCIES:** none (first agent to run).

**MUST NOT:** implement any page's actual content (that's every feature
agent's job — shell only provides the route and layout slot), define any
API call, define any component beyond the layout/nav shell itself (that's
`frontend-design-agent`'s `components/`).

---

## `frontend-design-agent`

**OWNS:** `src/components/**` (the full primitive list in
[design-system.md](design-system.md#component-primitives-owned-by-frontend-design-agent)),
Tailwind config, the color/typography tokens.

**INPUTS:** [design-system.md](design-system.md) in full — the status
badge category mapping, the "one primary action per screen" rule, the
responsive breakpoint behavior for `Table`.

**OUTPUTS:** a Storybook-able (or equivalently isolated-preview) set of
components: `Button`, `Card`, `StatusBadge`, `Table`, `EmptyState`,
`ErrorState`, `Skeleton`, `Modal`, `Toaster`. Each `StatusBadge` variant
must be verifiably correct against the full enum table in
[design-system.md](design-system.md#status-badges) — every value of
`ResumeStatus`, `ApplicationStatus`, `OutreachStatus`, `ContactStatus`,
`JobProcessingStatus` renders a mapped color, none falls through to an
"unstyled" default.

**DEPENDENCIES:** none (parallel with shell/api).

**MUST NOT:** import from `api/` or any `features/` folder — these
components take data via props only, never fetch anything themselves
(this is what makes them safely shared across 5 feature agents with zero
coordination needed).

---

## `frontend-api-agent`

**OWNS:** `src/api/**` in full, including `generated/schema.d.ts`'s
generation script.

**INPUTS:** [api-mapping.md](api-mapping.md) in full — every row of every
table is one function this agent must produce; the query key convention
and wire-format notes (especially the base64 resume-upload encoding) in
[state-management.md](state-management.md) and
[api-mapping.md](api-mapping.md#resumeprofile-service).

**OUTPUTS:** one typed function per documented action (e.g.
`approveOutreach(outreachId, body): Promise<OutreachResponse>`), one
`useQuery`/`useMutation` hook per action wrapping it with the correct query
key and invalidation, `client.ts`'s `ApiError` parsing per
[error-handling.md](error-handling.md).

**DEPENDENCIES:** none (parallel with shell/design), but every feature
agent depends on this one — it should be prioritized to finish first among
the three foundational agents if scheduling requires ordering.

**MUST NOT:** implement any UI, decide navigation, or add business logic
beyond parsing responses/errors (no re-implementing what the backend
already validated — see
[architecture.md#thin-client-principle](architecture.md#thin-client-principle)).
Must not hand-write types that duplicate `generated/schema.d.ts` — every
request/response type is imported from there.

---

## `frontend-profile-agent`

**OWNS:** `src/features/identity/**`, `src/features/preferences/**`,
`src/features/resumes/**` — pages: `/welcome`, `/settings`, `/resumes`.

**INPUTS:** `api/users.ts`, `api/resumes.ts`, `api/profiles.ts` hooks
(from `frontend-api-agent`); `components/*` primitives (from
`frontend-design-agent`); the per-page contract in
[routes.md](routes.md#welcome--onboarding),
[routes.md](routes.md#settings--search-preferences),
[routes.md](routes.md#resumes--resume-management).

**OUTPUTS:** working `/welcome`, `/settings`, `/resumes` pages matching
every loading/empty/error state documented for them in
[routes.md](routes.md), including the resume-parsing polling behavior in
[async-workflows.md](async-workflows.md#resume-parsing-progressive-disclosure).

**DEPENDENCIES:** `frontend-api-agent`, `frontend-design-agent`,
`frontend-shell-agent`.

**MUST NOT:** implement upload/base64-encoding logic itself (that's inside
`api/resumes.ts`, owned by `frontend-api-agent` — this agent only calls the
hook), touch `features/opportunities/`, `features/contacts/`,
`features/outreach/`.

---

## `frontend-opportunities-agent`

**OWNS:** `src/features/opportunities/**` — the Dashboard's primary-action
and pipeline-summary sections (`/`), `/opportunities`,
`/opportunities/:applicationId` including the lifecycle Timeline and
Status Action Menu.

**INPUTS:** `api/jobs.ts`, `api/matching.ts`, `api/tracking.ts`,
`api/profiles.ts` hooks; `components/*` primitives; `ContactsPanel` (a
finished component from `frontend-contacts-agent`, imported, never
modified) and a read-only outreach-summary display (this agent renders
`OutreachResponse` fields directly via `api/outreach.ts`'s read hooks —
see note below); the full per-page contract in
[routes.md](routes.md#opportunities--opportunities) and
[routes.md](routes.md#opportunitiesapplicationid--opportunity-detail);
the two previously-flagged BLOCKER gaps in that section (Job Information
panel, "other resumes evaluated") — both resolved as of Step 10.5 (see
[api-mapping.md#backend-gaps-affecting-this-mapping](api-mapping.md#backend-gaps-affecting-this-mapping)),
so this agent implements both sub-sections directly against the now-complete
`JobResponse`/`JobMatchResponse` fields, no workaround needed.

**OUTPUTS:** working `/`, `/opportunities`, `/opportunities/:applicationId`
pages, the status-grouping tab logic from
[user-flows.md](user-flows.md#application-tracker-ux), the progressive
per-status panel rendering from
[async-workflows.md](async-workflows.md#full-pipeline-progression-on-opportunity-detail-no-page-reload).

**DEPENDENCIES:** `frontend-api-agent`, `frontend-design-agent`,
`frontend-shell-agent`, `frontend-contacts-agent` (for the finished
`ContactsPanel` component specifically — everything else is independent).

**MUST NOT:** implement outreach approve/reject/edit actions (this page
only *reads* outreach state and links out to `/outreach/:outreachId` for
the actual review — that surface belongs entirely to
`frontend-outreach-agent`), re-implement contact ranking display logic
inside its own folder instead of importing `ContactsPanel`, invent
client-side scoring/recommendation logic to fill the two flagged gaps.

---

## `frontend-contacts-agent`

**OWNS:** `src/features/contacts/**` — `ContactsPanel` and its empty/error
states, exported for `frontend-opportunities-agent` to consume.

**INPUTS:** `api/contacts.ts` hooks; `components/*` primitives; the
contract in [routes.md](routes.md#opportunitiesapplicationid--opportunity-detail)'s
contacts panel entry and [user-flows.md](user-flows.md#contact-discovery-ux).

**OUTPUTS:** `ContactsPanel` accepting a `jobId` prop, self-contained
(fetches its own data, handles its own loading/empty/error state), plus
the "Search again" manual re-trigger action
(`POST /jobs/{job_id}/contacts/search`) documented as a real, supported
capability in [api-mapping.md](api-mapping.md#contact-discovery-service).

**DEPENDENCIES:** `frontend-api-agent`, `frontend-design-agent`.

**MUST NOT:** know about `Application`/`ApplicationStatus` or any other
opportunity-level concept beyond the `jobId` it's given — this component
must be usable with only a job id, nothing else, so it stays a true black
box for `frontend-opportunities-agent`.

---

## `frontend-outreach-agent`

**OWNS:** `src/features/outreach/**` — `/outreach`, `/outreach/:outreachId`,
the `OutreachReviewPanel` component (shared between the queue and the deep
link — not two implementations).

**INPUTS:** `api/outreach.ts` hooks; `components/*` primitives; the full
contract in [routes.md](routes.md#outreach--outreach-queue) and
[routes.md](routes.md#outreachoutreachid--outreach-review-deep-link); the
full review-flow diagram in
[user-flows.md](user-flows.md#outreach-review-ux); the explicit
"Generated ≠ Sent" requirement and the `409` conflict handling in
[error-handling.md](error-handling.md).

**OUTPUTS:** working `/outreach` and `/outreach/:outreachId`, the
approve/edit/reject actions wired to their exact documented endpoints, the
status-badge distinction between `APPROVED` and `SENT` never blurred in
copy or color.

**DEPENDENCIES:** `frontend-api-agent`, `frontend-design-agent`.

**MUST NOT:** add any action beyond approve/edit/reject — there is no
"send" button anywhere in this feature, because there is no send endpoint
(see [architecture.md#thin-client-principle](architecture.md#thin-client-principle));
must not implement its own copy of `StatusBadge` — reuses
`frontend-design-agent`'s component with the existing category mapping.

---

## `integration-ui-agent`

**OWNS:** `src/tests/e2e/**`, cross-feature wiring glue that doesn't
belong to any single feature (e.g. Dashboard's "needs attention" list
linking into `/outreach/:outreachId`, nav badge counts pulling from
multiple features' query hooks), a final pass reconciling loading/empty/
error state consistency across all pages against
[routes.md](routes.md)'s per-page tables.

**INPUTS:** every other agent's finished output; the golden paths in
[testing-strategy.md](testing-strategy.md#what-playwright-should-cover-minimum-golden-paths).

**OUTPUTS:** a working Playwright suite covering the four golden paths;
any small cross-feature glue components (e.g. a `NeedsAttentionList` on
the Dashboard that reads from both `applications` and `outreach` query
hooks — this genuinely doesn't belong to either `frontend-opportunities-agent`
or `frontend-outreach-agent` alone, since it reads both).

**DEPENDENCIES:** all seven other agents (runs last).

**MUST NOT:** modify another agent's feature folder to fix a bug found
during integration — report it back to the owning agent, per the same
"prefer a contract over reaching into internal state" discipline the
backend agents followed all project. Must not add new pages, new backend
calls beyond what [api-mapping.md](api-mapping.md) already documents, or
new product features — same explicit boundary the backend's
`integration-agent` had in Step 9.
