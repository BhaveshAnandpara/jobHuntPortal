# Testing Strategy

No tests are written this step (architecture only). This defines what each
layer is *for*, so implementing agents write tests in the right place
instead of everything as one kind.

## Layers

| Layer | Tool | What it covers | What it does NOT cover |
|---|---|---|---|
| Unit | Vitest | Pure functions: enum→label mappings (`ApplicationStatus` → "Analyzing...", `ContactType` → "Hiring Manager"), date/status-grouping helpers ([user-flows.md](user-flows.md#application-tracker-ux)'s tab grouping), the `api/client.ts` error-parsing function, the base64 encode helper for resume upload | Anything requiring a rendered component or a network layer |
| Component | Vitest + React Testing Library | Individual components in isolation: does `StatusBadge` render the right color/label for each `OutreachStatus`, does `EmptyState` show the right copy for each documented empty-state variant in [routes.md](routes.md), does the Outreach Review panel disable Approve while a mutation is in flight | Real API calls (mocked via MSW — see below), cross-page navigation |
| Integration (mocked backend) | Vitest + RTL + MSW | Full page behavior against a mocked API: submitting the job URL form and seeing the loading→success→error states, the polling behavior in [async-workflows.md](async-workflows.md) advancing a page through statuses on successive mocked responses, the approve/reject/edit flow's optimistic-vs-confirmed UI states | Real backend, real Kafka timing |
| End-to-end | Playwright | The golden paths, against a real running backend (the one already validated end-to-end in Step 9) or the same deterministic in-memory Kafka broker/SQLite harness `tests/integration/conftest.py` already builds for backend tests, fronted by the real FastAPI app | Exhaustive per-component variation — that's the layers above |

## MSW handler source

MSW request handlers should be generated/kept in sync from the same
OpenAPI schema used for types (see
[state-management.md#type-strategy](state-management.md#type-strategy)),
not hand-maintained fixture data that silently drifts from the real
response shape. A shared `tests/mocks/handlers.ts` per feature folder,
each handler explicitly modeling the states in
[routes.md](routes.md)'s per-page loading/empty/error tables (e.g. an
`outreach` handler that can be told to return an empty list, a populated
list, or a `409` on approve) is the concrete deliverable — not one big
undifferentiated mock file.

## What Playwright should cover (minimum golden paths)

1. First visit → create identity → upload a resume → see it reach
   `PARSED`.
2. Paste a job URL → see it appear in `/opportunities` → (using a
   deterministic backend fixture that fast-forwards through matching) see
   it reach `OUTREACH_GENERATED` → approve the outreach → see status
   become `SENT`.
3. Reject an outreach draft → confirm it never appears as sent, and the
   opportunity's timeline reflects the rejection.
4. An invalid job URL → inline error, no navigation.

These map directly onto the backend's own already-validated
`tests/integration/test_full_job_flow.py` and
`tests/integration/test_human_approval.py` scenarios (Step 9) — the
frontend e2e suite is verifying the *UI's* representation of flows the
backend has already proven correct, not re-proving backend correctness.

## Ownership

Each feature agent (see [agent-ownership.md](agent-ownership.md)) owns
unit/component/integration tests for its own folder. `integration-ui-agent`
owns the Playwright suite exclusively, since golden-path e2e tests
necessarily cross feature boundaries.
