import { defineConfig, devices } from '@playwright/test'

/**
 * See docs/frontend/testing-strategy.md#what-playwright-should-cover-minimum-golden-paths.
 * Owner: frontend-integration-ui-agent.
 *
 * `webServer` is an array of two independently-managed processes:
 *   1. The real Vite dev server (unchanged from the skeleton step).
 *   2. `e2e/backend_server.py` (repo root, outside both `src/` and
 *      `tests/` — see that file's header) — a real `uvicorn` process
 *      serving the real `api.main.app`, wired to a fully deterministic
 *      fake backend (one shared in-memory SQLite DB + Kafka broker, a
 *      background task continuously draining it) so the browser observes
 *      the async jobs.discovered -> ... -> outreach.sent pipeline actually
 *      advance, over real HTTP, without any real Ollama/Kafka/Postgres.
 *      Health-checked against `/openapi.json`, a stock FastAPI endpoint —
 *      no new backend route was added for this.
 *
 * `python` is invoked bare (not `python3`/a venv path) because this repo's
 * own convention is `python -m pytest tests -q` from the repo root already
 * works directly — see e2e/backend_server.py's header for why the same
 * plain `python` on PATH is sufficient here too.
 */
export default defineConfig({
  testDir: './tests/e2e',
  // Deliberately serial (fullyParallel: false, workers: 1), not the
  // skeleton's default. Every spec in this suite drives the SAME shared
  // e2e/backend_server.py process — one in-memory SQLite engine, one
  // InMemoryBroker, one 250ms drain loop. Confirmed empirically: multiple
  // full resume->job->match->contacts->outreach pipelines racing through
  // that single process concurrently (the skeleton's fullyParallel: true
  // default, even at workers: 2) produced real, reproducible timeouts —
  // Applications not yet visible, contacts not yet found, etc. — not
  // because the pipeline logic was wrong (every one of these same flows
  // passes reliably in isolation, see this agent's final report), but
  // because concurrent load against one lightweight fake backend process
  // isn't the same deployment shape a multi-worker Kafka consumer group
  // would have. Trading suite wall-clock time for determinism here is the
  // right call for THIS harness's architecture.
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 2 : 0,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: 'npm run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: !process.env.CI,
    },
    {
      command: 'python ../e2e/backend_server.py',
      cwd: import.meta.dirname,
      url: 'http://127.0.0.1:8000/openapi.json',
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
  ],
})
