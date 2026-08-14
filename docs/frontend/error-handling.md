# Error Handling

## The one error shape

Confirmed identical across all eight routers (see
[api-mapping.md#error-contract-applies-to-every-table-above](api-mapping.md#error-contract-applies-to-every-table-above)):

```ts
type ApiError = {
  status: number;       // HTTP status from the response
  code: ErrorCode;       // shared/errors/codes.py enum value, e.g. "NOT_FOUND"
  message: string;       // human-readable, backend-authored
};
```

`api/client.ts` (owned by `frontend-api-agent`) parses every non-2xx
response into exactly this shape once, at the lowest layer, so no feature
module ever hand-parses a raw `Response` or `fetch` rejection.

## Presentation by scenario

| Scenario | Presentation | Why |
|---|---|---|
| Mutation fails (approve/reject/edit/upload/save preferences/status change) | Toast (`sonner`), error variant, shows `message` verbatim | The user just took an action and is looking at the thing they acted on — a toast confirms/denies without navigating them away from their place |
| Primary page query fails (`GET /applications/{id}` 404, `GET /applications` fails entirely) | Page-level error state with a retry button | This *is* the page's content; there's nothing else to show underneath it |
| Secondary panel query fails (job detail, contacts, outreach summary on Opportunity Detail) | Inline, panel-scoped error with retry, rest of the page stays interactive | One failing panel must never take down panels that loaded fine — this is why Opportunity Detail's 7 API calls (see [routes.md](routes.md#opportunitiesapplicationid--opportunity-detail)) are independent queries, not one combined fetch |
| `409 CONFLICT` on outreach approve/reject (already decided elsewhere) | Specific inline message ("This was already decided") + automatic refetch of that item, not a generic toast | A real, expected concurrent-edit case documented in `api-contracts.md`, not a bug — treating it identically to an unexpected 500 would mislead the user into retrying something that isn't actually broken |
| `400 VALIDATION_ERROR` on a form submit (preferences, edit outreach message, create identity) | Inline field-level error using `message`, form stays populated | Never discard user input on a validation failure |
| `INVALID_JOB_URL` (400, on `POST /jobs/ingest-url`) | Inline error under the URL input | Same pattern as generic `VALIDATION_ERROR`, distinguished only by copy if the message benefits from it |
| Network failure / API completely unreachable (no response at all, not a 4xx/5xx) | Persistent top-of-app banner ("Can't reach the server"), not a toast that disappears | A toast is wrong here because the condition doesn't resolve itself — the banner should clear automatically once a query succeeds again, not require dismissal |
| Render-time exception (a genuine frontend bug — malformed data, etc.) | React error boundary per route, "Something went wrong" + reload action, never a blank white screen | Last-resort safety net, not a designed-for path |

## What never happens

- Raw Python tracebacks, stack traces, or infrastructure error text (Kafka,
  SQLAlchemy, LangGraph internals) reaching the UI — the backend's own
  `ErrorCode` contract already guarantees this is never in `message`
  (`shared/errors/codes.py` is a closed, small enum of business-meaningful
  codes), so the frontend's job is just to not add a second, less careful
  path to the same interface (e.g. dumping a raw `fetch` rejection's
  `.stack` into the DOM).
- Retrying a `409` automatically without user action — refetching to show
  the *current* correct state (see the outreach conflict row above) is not
  the same as blindly re-sending the same mutation, which could double-act
  on stale intent.
- Silent failure — every mutation has a visible success or failure
  signal; there is no "fire and forget" action anywhere in this app,
  matching the deliberateness the backend already enforces for outreach.

## Invalid-state-transition UX (a specific case of `400 VALIDATION_ERROR`)

`PATCH /applications/{id}/status` rejects invalid transitions per
`state-machines.md#opportunity-lifecycle`. The Status Action Menu on
Opportunity Detail pre-filters its own options to the transitions the enum
plausibly allows from the current status (a UX nicety — see
[architecture.md#thin-client-principle](architecture.md#thin-client-principle)
for why this filtering is not itself the authority). If the backend still
rejects a selected transition (e.g. a stale menu after a concurrent
update), the response's `message` is shown inline in the menu, and the
menu refetches the current status rather than leaving a now-wrong option
selected.
