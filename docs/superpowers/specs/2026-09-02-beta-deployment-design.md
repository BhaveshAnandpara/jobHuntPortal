# Beta Deployment Design

_Status: Approved for planning · 2026-09-02_

## Purpose

Get the AI-Powered Multi-Agent Career Opportunity Platform live for a small
group of beta testers, using free-tier infrastructure only. This is the
first deployment of the project — today it only runs locally via
`docker-compose.yml` (Postgres + Kafka) plus natively-run Ollama, FastAPI,
consumers, and Vite dev server.

## Constraints (from stakeholder input)

- **Audience:** a small group of beta testers (more than just the owner,
  not a public launch). Multi-user auth already exists (`src/users/`), no
  new work needed there.
- **Budget:** free-tier tools only.
- **LLM provider:** switch from Ollama (native, host-only) to **Groq** for
  the deployed environment. Ollama remains the local-dev default; nothing
  about local dev changes. The provider abstraction already supports this
  (`infrastructure/llm/groq_provider.py` exists and is tested).
- **Frontend host:** Vercel (already decided by stakeholder).
- **Backend host:** stakeholder deferred to recommendation — see Approach
  section.

## Approaches considered

### A — Single always-free VM for the whole backend (chosen)

Run Postgres + Kafka + the FastAPI API + the Kafka consumer workers all on
one Oracle Cloud "Always Free" VM via an extended docker-compose stack.
Vercel hosts the frontend and calls the VM's public HTTPS API.

- **Pros:** Reuses the existing `docker-compose.yml` almost as-is (just adds
  two more services). No per-service free-tier quotas, idle spin-down, or
  quiet expirations to work around. Genuinely $0/month indefinitely.
- **Cons:** Stakeholder owns OS patching/security on the VM. Oracle's free
  ARM capacity is occasionally hard to claim on first signup (region
  dependent — plan a fallback region or the AMD always-free shape if so).
  No managed backups; a backup cron for Postgres is the operator's
  responsibility.

### B — Fully managed piecemeal (rejected)

Vercel (frontend) + Render/Fly.io (API) + Neon/Supabase (Postgres) +
Upstash Kafka (Kafka-compatible, real free tier).

- **Pros:** Nothing to patch; each piece scales independently later.
- **Cons (why rejected):** The Kafka **consumer workers** are the
  dealbreaker. Render's free tier only offers "Web Services" (request-
  triggered, spins down after 15 min idle — fatal for a process that must
  stay subscribed to Kafka topics) and "Static Sites" — not standalone
  background workers. A real background worker needs Render's paid tier
  (~$7/mo), which violates the free-tier constraint. Fly.io no longer
  offers a true no-card free allowance either. More accounts/secrets to
  wire together for no benefit at this stage.

### C — Skip Kafka, run as a single process (rejected)

Use the codebase's existing in-memory fake broker
(`infrastructure/kafka/in_memory.py`) instead of a real Kafka broker, so
the whole app is one deployable process.

- **Why rejected:** Verified against the actual code — that fake broker is
  explicitly a single-process test fixture (`in_memory.py`'s own docstring:
  exists so tests can run "without a running Kafka broker"). The API and
  consumer processes run as separate OS processes in this architecture, so
  they can't share an in-process fake broker across that boundary without
  new code. Writing that code defeats the purpose of a quick beta deploy
  and would diverge from the architecture beta testers are meant to
  validate. YAGNI — not worth it for this stage.

## Chosen Architecture (Approach A)

```text
Vercel (React/Vite frontend)
        │  HTTPS
        ▼
Oracle Cloud "Always Free" VM
  ┌─────────────────────────────────┐
  │ docker compose (prod overlay):  │
  │  - postgres   (existing)        │
  │  - kafka      (existing)        │
  │  - api        (NEW)             │
  │  - consumers  (NEW)             │
  │  - caddy      (NEW — HTTPS)     │
  └─────────────────────────────────┘
        │
        ▼
   Groq API (LLM, external, HTTPS out)
```

## Components

1. **Backend `Dockerfile`** (new) — installs the package
   (`pip install -e .`), single image reused for both the `api` service
   (command: `python scripts/run_server.py`) and the `consumers` service
   (command: `python scripts/run_consumers.py`).
2. **`docker-compose.prod.yml`** (new, overlay file — local dev's
   `docker-compose.yml` stays untouched) adding the `api`, `consumers`, and
   `caddy` services alongside the existing `postgres`/`kafka` services.
3. **Caddy reverse proxy** (new service) — terminates HTTPS with an
   automatic Let's Encrypt cert, needs only a domain name pointed at the
   VM's IP; routes `/` to the `api` container's port 8000.
4. **`.env.production`** on the VM (never committed) with:
   - `DATABASE_URL` → compose `postgres` service
   - `KAFKA_BOOTSTRAP_SERVERS` → compose `kafka` service
   - `LLM_PROVIDER=groq`, `GROQ_API_KEY`
   - `JWT_SECRET`
   - `CORS_ORIGINS` including the Vercel deployment URL (CORS handling
     already exists in `src/api/main.py` — config only, no new code)
5. **Domain name** for the VM — required for HTTPS. A free subdomain
   (Cloudflare or DuckDNS) is sufficient if the stakeholder doesn't already
   own a domain.
6. **Vercel project config** — `VITE_API_BASE_URL=https://api.<domain>`
   (frontend already reads this exact env var in `frontend/src/api/client.ts`,
   defaulting to `http://localhost:8000` today — config only, no new code).

## Data flow

Beta tester's browser → Vercel-hosted frontend → HTTPS call to the VM's API
→ FastAPI handles the request, publishes a Kafka event if applicable →
`consumers` container (same VM, same docker network) picks it up
asynchronously → writes to Postgres. The frontend already polls for
updates (`frontend/src/hooks/usePolling.ts`), so no websockets or extra
real-time infrastructure is needed for this stage.

## Secrets & error handling

All secrets live only in the VM's `.env.production`; nothing beyond the
public API base URL is ever sent to Vercel. For a beta-scale deployment,
`docker compose logs -f` on the VM is sufficient observability — no
additional monitoring stack needed yet.

## Testing / rollout plan

1. Deploy the stack to the VM.
2. Run the existing Playwright e2e suite (`frontend/tests/e2e/`) against
   the live URL once, as a smoke test, before inviting testers.
3. Run `python scripts/reset_db.py` to hand beta testers a clean database.

## Out of scope for this deployment

- Real job-board integration, real outreach sending, and profile-update
  re-matching remain unimplemented (see `PROJECT_STATUS.md`) — this
  deployment does not change that; testers exercise the same feature set
  that exists locally today.
- Analytics dashboard — not built yet, not part of this deployment.
- CI/CD automation (auto-deploy on push) — not requested; this is a manual
  deploy for a beta, not a pipeline. Can be a follow-up.
- High availability / autoscaling — explicitly out of scope at beta-tester
  scale.
