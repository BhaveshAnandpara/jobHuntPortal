# Frontend Documentation

Step 10 deliverable: architecture and planning for the client that sits on
top of the validated backend MVP (616 backend tests passing, full
end-to-end integration validated — see the Step 9 Integration Report).
**No frontend code exists yet.** This directory is architecture-only, per
the explicit scope boundary for this step.

## Reading order

1. [architecture.md](architecture.md) — stack, thin-client principle, high-level shape.
2. [routes.md](routes.md) — page-by-page information architecture.
3. [user-flows.md](user-flows.md) — end-to-end and feature-level UX flows.
4. [api-mapping.md](api-mapping.md) — every frontend action mapped to a real backend endpoint.
5. [state-management.md](state-management.md) — server/client/persisted state, and the types strategy.
6. [async-workflows.md](async-workflows.md) — how the UI observes multi-stage backend processing.
7. [error-handling.md](error-handling.md) — error model, loading/empty states.
8. [testing-strategy.md](testing-strategy.md) — Vitest/RTL/MSW/Playwright layers.
9. [design-system.md](design-system.md) — visual direction and component primitives.
10. [repository-structure.md](repository-structure.md) — proposed `frontend/` layout.
11. [agent-ownership.md](agent-ownership.md) — parallel implementation agent boundaries and contracts.

## Source-of-truth documents this was derived from

`about_project.md`, `CLAUDE.md`, `docs/architecture/*.md` (all 12 files),
`src/shared/types/api/*.py`, `src/shared/types/dto.py`,
`src/shared/types/enums.py`, `src/shared/errors/codes.py`, `src/api/main.py`,
and the actual mounted FastAPI routes (`src/*/api/routes.py`,
`src/jobs/ingestion/api.py`, `src/jobs/discovery/api.py`) — read directly
rather than trusted from `api-contracts.md` prose alone, which surfaced two
real prose/implementation mismatches documented in
[api-mapping.md](api-mapping.md#backend-gaps-affecting-this-mapping).

## What this frontend is not

It does not do matching, ranking, scoring, outreach generation, approval
authorization, or lifecycle transitions. Every one of those already exists,
tested, in the backend. The frontend's only job is to render backend state
and submit backend-defined actions — see
[architecture.md#thin-client-principle](architecture.md#thin-client-principle).

## Status

Architecture only. Awaiting explicit approval before any `frontend/`
repository, package, or component is created.
