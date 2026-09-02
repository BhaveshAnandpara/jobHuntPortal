# Deployment to Beta: Product Specification

_Status: For Implementation · 2026-09-02_

## Overview

Deploy the AI-Powered Multi-Agent Career Opportunity Platform to a publicly accessible beta environment, enabling a small group of beta testers to use the platform via web browser without local setup.

**Target audience:** Small group of beta testers (invite-only, multi-user auth already implemented)  
**Success measure:** Beta testers can sign in, use all existing features (job discovery, profile management, agent reasoning), and receive feedback via the live platform.

## User Experience Goals

### For Beta Testers

1. **Discoverability:** Access the platform via a public HTTPS URL (no localhost)
2. **Seamless sign-in:** Existing multi-user auth (`src/users/`) works without change
3. **Feature parity:** All locally-working features function identically on deployed instance
4. **Feedback loop:** Platform performs well enough for testers to find and report bugs without infrastructure issues

### For the Operator (you)

1. **Low operational burden:** No daily monitoring needed; `docker compose logs -f` is sufficient
2. **Cost-free operation:** Sustains indefinitely on free-tier infrastructure only
3. **Quick troubleshooting:** Logs and database state are directly accessible on the VM
4. **Clean testing:** Can reset the database for testers without losing code

## Behavior & Invariants

### Vercel Frontend (React/Vite)

- Served via Vercel's free tier at a public HTTPS URL
- Reads `VITE_API_BASE_URL` env var to locate the backend API
- Falls back to `http://localhost:8000` for local development (unchanged)
- **Acceptance:** Frontend loads, sign-in form renders, API calls route to the configured backend

### Oracle Cloud VM Backend Stack

The entire backend runs on a single "Always Free" VM via an extended `docker-compose` stack:

- **Postgres:** Existing service (unchanged from local dev)
- **Kafka:** Existing service (unchanged from local dev)
- **FastAPI API:** New service, runs `python scripts/run_server.py`, accessible on port 8000
- **Kafka consumers:** New service, runs `python scripts/run_consumers.py`, subscribes to Kafka topics
- **Caddy:** New reverse proxy service, terminates HTTPS with auto-renewed Let's Encrypt cert, routes traffic to the API

**Acceptance:** All services start without errors; Caddy binds to port 443 (HTTPS), API responds on port 8000.

### LLM Provider Switch

- Local dev: Ollama (native, unchanged)
- Deployed: Groq (free-tier API key provisioned)
- **Acceptance:** API calls to `/chat` or other LLM endpoints successfully route to Groq; responses are identical in structure to local Ollama responses

### Secrets & Configuration

- All production secrets (Groq API key, JWT secret, database password) live only in `.env.production` on the VM
- Never committed to the repo
- Frontend only receives the public API base URL; no secrets ever leave the VM
- **Acceptance:** No secrets appear in Vercel build logs, GitHub, or frontend network requests

### Continuity & Recovery

- No managed backups initially; operator can manually back up Postgres on-demand
- Database can be reset to a known clean state with `python scripts/reset_db.py` before inviting new testers
- **Acceptance:** `reset_db.py` drops and recreates all tables; testers see a clean slate

## Success Criteria

### Deployment Success

1. ✓ Vercel frontend deploys and loads at a public HTTPS URL
2. ✓ Oracle Cloud VM is provisioned and running Docker
3. ✓ Caddy reverse proxy terminates HTTPS and routes to the API
4. ✓ FastAPI API responds to requests from the frontend
5. ✓ Kafka consumers are subscribed and processing events
6. ✓ Groq LLM provider is wired and responding to API requests
7. ✓ Multi-user sign-in works; testers can authenticate and see their own data

### Testing & Readiness

1. ✓ Playwright e2e test suite passes against the live deployment URL (smoke test)
2. ✓ Manual feature walkthrough confirms all locally-working features work on deployed instance
3. ✓ Database reset completes cleanly, ready for beta testers
4. ✓ Deployment runbook documented (how to start/stop/debug the stack, where secrets are)

### Operational Readiness

1. ✓ Operator can view logs via `docker compose logs -f`
2. ✓ Operator can restart individual services without downtime to others
3. ✓ Operator can update `.env.production` and redeploy without re-provisioning the VM

## Non-Goals & Out of Scope

- Real job-board integration and outreach sending (remains unimplemented)
- Analytics dashboard
- CI/CD automation (manual deploy only)
- High availability, autoscaling, or managed backups
- Real-time features (websockets, live collaboration) — polling suffices for this stage

## Risk Mitigation

### LLM Provider Switch (Groq)

- **Risk:** Groq API has different rate limits or response format than Ollama
- **Mitigation:** Already tested via `infrastructure/llm/groq_provider.py` and its test suite; verified to be drop-in compatible
- **Acceptance:** Existing test coverage for `groq_provider.py` passes; no new code needed

### Free-Tier VM Availability

- **Risk:** Oracle Cloud "Always Free" ARM capacity occasionally unavailable in signup's default region
- **Mitigation:** Fallback to AMD always-free shape or a different region
- **Acceptance:** VM is provisioned and pingable from the internet

### SSL/HTTPS Certificate

- **Risk:** Let's Encrypt rate limits or cert renewal failure
- **Mitigation:** Caddy auto-renews; only 1 domain in scope; low renewal rate
- **Acceptance:** Caddy successfully obtains cert; HTTPS handshake succeeds; certificate auto-renews without manual intervention

### Operator Responsibility

- **Risk:** Operator forgets to patch the VM; security vulnerabilities accumulate
- **Mitigation:** For beta-stage, acceptable risk; can migrate to managed infrastructure later if needed
- **Acceptance:** Documented in runbook that operator owns OS patching

## Rollout & Validation Plan

1. **Deploy to VM:** Extend `docker-compose.yml`, add `.env.production`, start the stack
2. **Smoke test:** Run Playwright e2e suite against the live URL once; all tests pass
3. **Manual walkthrough:** Operator signs in, creates a profile, triggers an agent to reason; verifies all features work
4. **Database reset:** Run `python scripts/reset_db.py`; verify all tables are fresh
5. **Invite beta testers:** Share the HTTPS URL; invite users to sign in
6. **Monitor & gather feedback:** Watch `docker compose logs` for errors; collect tester feedback via Slack/email

## References

- Approved design: `docs/superpowers/specs/2026-09-02-beta-deployment-design.md`
- Frontend client config: [frontend/src/api/client.ts](frontend/src/api/client.ts)
- API CORS config: [src/api/main.py](src/api/main.py)
- LLM provider abstraction: [infrastructure/llm/groq_provider.py](infrastructure/llm/groq_provider.py)
- E2E test suite: [frontend/tests/e2e/](frontend/tests/e2e/)
- Database reset script: `python scripts/reset_db.py`
