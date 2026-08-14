# Career Opportunity Platform — Frontend

Architecture and planning: `../docs/frontend/`. Read `../docs/frontend/README.md`
first. This is the skeleton created in Step 11 — structural foundation only,
no product features implemented yet.

## Local development

```bash
npm install
cp .env.example .env.local   # set VITE_API_BASE_URL if not the default
npm run dev
```

Requires the backend running locally (`uvicorn api.main:app` from the repo
root, or however the backend's own dev workflow starts it) for any real API
call to succeed — the skeleton's placeholder pages don't call the backend
yet.

## Regenerating API types

Backend response/request types are generated from the FastAPI OpenAPI
schema, never hand-duplicated — see
`../docs/frontend/state-management.md#type-strategy`.

```bash
# Normal workflow: backend dev server running at localhost:8000
npm run generate:api-types

# Offline / no server running: regenerate from the committed static
# snapshot instead (openapi.snapshot.json — refresh it with the python
# one-liner below whenever the backend's contracts change, then re-run
# this script)
npm run generate:api-types:snapshot
```

To refresh `openapi.snapshot.json` from the backend without starting a
server (run from the repo root, in the backend's Python environment):

```bash
python -c "import json; from api.main import app; json.dump(app.openapi(), open('frontend/openapi.snapshot.json', 'w'), indent=2)"
```

Output: `src/api/generated/schema.d.ts` (never hand-edited — see that
file's own header). `src/api/types.ts` aliases the names every feature
actually imports.

## Scripts

| Script | Purpose |
|---|---|
| `npm run dev` | Vite dev server |
| `npm run build` | Typecheck (`tsc -b`) + production build |
| `npm run preview` | Preview a production build locally |
| `npm run typecheck` | Typecheck only, no build output |
| `npm run lint` | oxlint |
| `npm test` | Vitest, single run |
| `npm run test:watch` | Vitest, watch mode |
| `npm run e2e` | Playwright (starts the dev server itself — see `playwright.config.ts`) |
| `npm run generate:api-types` | Regenerate `src/api/generated/schema.d.ts` from a running backend |
| `npm run generate:api-types:snapshot` | Same, from the committed `openapi.snapshot.json` |

## Ownership

Every directory under `src/` has a single documented owner — see
`../docs/frontend/agent-ownership.md` and
`../docs/frontend/repository-structure.md`'s ownership map. Frontend
implementation agents are defined in `../.claude/agents/frontend-*.md`.
