# State Management

## Three categories

### Server state

Everything in [api-mapping.md](api-mapping.md) — resumes, profiles, jobs,
matches, contacts, outreach, applications, preferences. Owned entirely by
TanStack Query. Never copied into a separate store: components read
directly from `useQuery`/`useMutation`, and cache invalidation (not manual
`setState`) is how "the backend changed, re-render" propagates. This is
the deliberate consequence of the thin-client principle in
[architecture.md](architecture.md) — if server state were duplicated into
a client store, that store would become a second source of truth the
frontend would have to keep in sync itself, which is exactly the kind of
logic that belongs in the backend, not React.

### Client/UI state

Modal open/closed, active tab, filter selection, in-progress (unsaved) form
values, "which outreach item is expanded." All local `useState`/`useReducer`
inside the component that owns the UI, or lifted at most to the nearest
shared ancestor (e.g. `StatusFilterTabs`'s selected tab lives in
`/opportunities`'s route component, not a global store — it's meaningless
outside that page and resets on navigation, which is the correct behavior,
not a bug to work around with persistence).

### Persisted user state

Exactly one item: the active `user_id`, written to `localStorage` at
`/welcome` and read by a small `IdentityContext` (React Context, not a
state library) at app startup. This is the only client-persisted state in
the whole app — see [architecture.md#no-authentication-layer-yet](architecture.md#no-authentication-layer-yet)
for why this exists at all (there is no backend session to read it from
instead). `UserPreferences` is *not* duplicated into local storage — it is
server state (`GET/PUT /users/{id}/preferences`), fetched fresh each
session like everything else in the server-state category.

## Why no Zustand/Redux

The only genuinely cross-cutting client state in this app is the active
`user_id` (persisted, see above) and toast notifications (owned entirely
by `sonner`'s own internal state, never touched directly). Neither needs a
general-purpose state library — React Context covers the first, a
dedicated toast library covers the second. Introducing Redux or Zustand
here would mean maintaining a third state mechanism (alongside TanStack
Query for server state and local `useState` for UI state) for a state
surface area of exactly one value. If a second genuinely global,
cross-feature client concern appears later (e.g. a persistent sidebar
collapse preference), Context remains sufficient for that scale too — this
isn't a "for now" deferral, it's a sizing judgment that should be
revisited only if a real need for shared, frequently-updated,
deeply-nested state actually appears.

## Type strategy

**Decision: generate from the FastAPI OpenAPI schema, via
`openapi-typescript` (dev-only, build-time, zero runtime dependency).**

```
FastAPI app (src/api/main.py)
        │  already auto-generates /openapi.json (built into FastAPI —
        │  no backend change needed to get this)
        ▼
openapi-typescript /openapi.json → src/api/generated/schema.d.ts
        │
        ▼
api/*.ts modules import request/response types from generated/schema.d.ts,
never redeclare them by hand
```

Why not hand-written frontend DTOs: every request/response type in
`src/shared/types/api/*.py` is already the locked, documented contract
(`repository-structure.md#contract-naming-conventions` — `Request`/
`Response` suffix rules, no redefinition of an entity already in
`domain-model.md`). Hand-copying these into TypeScript interfaces creates
exactly the kind of drift the backend's own architecture docs spend
significant effort preventing between Python components (see
`ownership.md`'s "prefer a contract over reaching into internal state"
rule) — there is no reason to reintroduce that drift risk at the
frontend/backend boundary specifically. Regenerating on every backend
schema change (a single CLI command, wired into a `package.json` script,
e.g. `npm run generate:types`) keeps the two in sync mechanically instead
of by discipline.

Why not a full codegen *client* (e.g. `orval`, `openapi-generator`'s
service-method output) on top of the generated types: the brief's section
14 explicitly asks for a documented `src/api/` boundary with one hand-written
module per backend service (`users.ts`, `jobs.ts`, etc.), each function
mapping to exactly one documented action in [api-mapping.md](api-mapping.md).
A full generated client would produce one function per *endpoint*
mechanically, which is close but not the same shape — and it would hide
the wire-format nuance already found in this document (the base64
`file_content` encoding for resume upload — see
[api-mapping.md](api-mapping.md#resumeprofile-service)) inside generated
code that regenerates over any hand-added special case on the next run.
Generating *types only*, and hand-writing the thin call functions around
them, keeps both benefits: no manual type drift, and full control over the
handful of endpoints that need slightly more than "pass the body through."

## Query key convention

One flat convention across all `api-agent`-owned hooks, so cache
invalidation across features stays predictable without a central registry:

```
['resumes', userId]
['resume', resumeId]
['profiles', userId]
['profile', profileId]
['jobs', jobId]                    (single job detail)
['match', jobId]
['contacts', jobId]
['outreach', userId, status?]
['outreach-item', outreachId]
['applications', userId, status?]
['application', applicationId]
['history', applicationId]
['preferences', userId]
```

Every mutation's `onSuccess` invalidates the specific keys it affects (see
the "State affected" column in [api-mapping.md](api-mapping.md)) — never a
blanket `queryClient.invalidateQueries()` with no key, which would
re-fetch unrelated data on every action and make cache behavior
unpredictable across features owned by different agents.
