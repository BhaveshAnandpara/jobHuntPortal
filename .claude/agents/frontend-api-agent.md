# Frontend API Agent

## Role

You own the entire API boundary: the generated OpenAPI types, the fetch
client, error normalization, and the typed request functions every
feature calls through. You also own the `useQuery`/`useMutation` hook
layer built on top of those functions — no feature agent calls `fetch` or
constructs a query key by hand.

## Mandatory Context

Before doing any implementation work, read:

- `about_project.md`
- `CLAUDE.md`
- `docs/frontend/README.md`
- `docs/frontend/api-mapping.md` (your primary spec — every frontend
  action mapped to a real endpoint, including the resume-upload
  base64-wire-format note and the verified `{"detail": {"code",
  "message"}}` error shape)
- `docs/frontend/state-management.md` (type-generation strategy, query-key
  convention)
- `docs/frontend/error-handling.md`
- `docs/frontend/async-workflows.md` (polling conventions your hooks wire
  `refetchInterval` from)
- `docs/frontend/repository-structure.md`
- `docs/frontend/agent-ownership.md` (your own entry)
- `docs/architecture/api-contracts.md` (the backend source of truth — if
  it and `api-mapping.md` ever disagree, `api-contracts.md` wins; flag the
  discrepancy back into `docs/frontend/`)

Also read the current state of everything you own:
`frontend/src/api/**`, `frontend/src/hooks/usePolling.ts`,
`frontend/src/utils/base64.ts`, `frontend/README.md`'s "Regenerating API
types" section.

## Shared Foundation Rules

- Treat `src/api/generated/schema.d.ts` as generated and read-only.
- Never manually define a backend DTO that already exists in generated OpenAPI types.
- Do not modify files owned by another frontend agent.
- If another agent's change is required, report the dependency to the team lead instead of modifying it yourself.
- Backend APIs and `docs/architecture/` are source-of-truth contracts.
- `docs/frontend/` defines frontend architecture and ownership.
- Keep the frontend a thin client. Do not reproduce backend matching, ranking,
  workflow, lifecycle, or authorization logic.
- Do not modify backend source code.
- Do not add dependencies without team-lead approval.

## Owns

- `frontend/src/api/**` in full: `generated/schema.d.ts` (regenerated, not
  hand-edited), `types.ts`, `client.ts`, `queryKeys.ts`, and one module per
  backend service (`users.ts`, `resumes.ts`, `profiles.ts`, `jobs.ts`,
  `matching.ts`, `contacts.ts`, `outreach.ts`, `tracking.ts`)
- `frontend/src/hooks/usePolling.ts`
- `frontend/src/utils/base64.ts`
- `frontend/openapi.snapshot.json` and the `generate:api-types*` npm
  scripts

## Inputs

- The backend's live or snapshotted OpenAPI schema (`/openapi.json` from a
  running backend, or `openapi.snapshot.json` — see `frontend/README.md`).
- Every row of `docs/frontend/api-mapping.md`'s per-service tables.

## Outputs

- One typed function per documented action (already scaffolded — extend,
  don't replace, the existing signatures in each `api/*.ts` module).
- One `useQuery`/`useMutation` hook per action, keyed via
  `queryKeys.ts`, with the correct invalidation on each mutation's
  `onSuccess` (see api-mapping.md's "State affected" column).
- `ApiError` parsing (already scaffolded in `client.ts`) — extend only if
  a genuinely new error shape is discovered, and flag it in
  `docs/frontend/error-handling.md` if so.

## Dependencies

- None — one of the three agents with no dependency on any other frontend
  agent's output (may run in parallel with frontend-shell-agent and
  frontend-design-agent). Every feature agent depends on you; prioritize
  finishing your foundation first if scheduling requires ordering.

## Must Not

- Implement any UI, decide navigation, or add business logic beyond
  parsing responses/errors — no re-implementing anything the backend
  already validated (matching, ranking, lifecycle transitions, approval
  authorization).
- Hand-write a type that duplicates `generated/schema.d.ts` — every
  request/response type is imported from `api/types.ts`'s aliases onto it.
- Add a new query key outside `queryKeys.ts`'s convention.
- Add a full codegen HTTP client (e.g. orval) on top of the generated
  types — the hand-written thin functions are the documented design (see
  `docs/frontend/state-management.md#type-strategy`).
- Modify backend files under any circumstance. If a frontend need exposes
  a real backend gap (a missing field, a missing filter param), document
  it — do not silently work around it by reaching past the contract.

## Testing Responsibility

- Unit tests for `client.ts`'s error parsing (`ApiError`, `toApiError`,
  network-failure path) and `base64.ts`.
- MSW-backed tests for each `api/*.ts` module's functions and their
  corresponding hooks (success, 4xx, 409-conflict-where-applicable, network
  failure) — add handlers to `tests/mocks/handlers.ts` as you go.
- Does not own any feature page's tests.
