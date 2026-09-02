# Deployment to Beta: Technical Specification

_Status: For Implementation · 2026-09-02_

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Vercel (Frontend)                           │
│  - React/Vite app, HTTPS via Vercel's CDN                          │
│  - Reads VITE_API_BASE_URL env var (e.g., https://api.example.com) │
│  - CORS origin whitelisted in backend (no-auth public endpoints)    │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ HTTPS
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│          Oracle Cloud "Always Free" VM (Single Instance)            │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ docker compose (prod overlay):                               │   │
│  │  ┌─────────────┐                                              │   │
│  │  │ Caddy       │  (port 443/80, reverse proxy, HTTPS)        │   │
│  │  └──────┬──────┘                                              │   │
│  │         │ localhost:8000                                      │   │
│  │  ┌──────▼──────┐     ┌──────────┐     ┌───────────────────┐  │   │
│  │  │ FastAPI API │────▶│ Postgres │     │ Groq API (ext)    │  │   │
│  │  │  (port 8000)│     │(port 5432)     │ (HTTPS egress)    │  │   │
│  │  └────┬────────┘     └──────────┘     └───────────────────┘  │   │
│  │       │  (publishes events)                                    │   │
│  │  ┌────▼──────────┐                                             │   │
│  │  │ Kafka broker  │─ topics: job_matches, profile_updates, etc  │   │
│  │  │ (port 9092)   │                                             │   │
│  │  └────▲──────────┘                                             │   │
│  │       │  (subscribes to all topics)                            │   │
│  │  ┌────┴─────────────────────┐                                  │   │
│  │  │ Consumer workers         │                                  │   │
│  │  │ (python scripts/...) ──▶ Postgres (writes)                 │   │
│  │  └──────────────────────────┘                                  │   │
│  │                                                                │   │
│  │ Config: .env.production (never committed)                     │   │
│  │  - DATABASE_URL=postgresql://...@postgres:5432/job_hunt      │   │
│  │  - KAFKA_BOOTSTRAP_SERVERS=kafka:9092                        │   │
│  │  - LLM_PROVIDER=groq                                         │   │
│  │  - GROQ_API_KEY=<secret>                                     │   │
│  │  - JWT_SECRET=<secret>                                       │   │
│  │  - CORS_ORIGINS=https://vercel-app-url.vercel.app            │   │
│  │  - CADDY_EMAIL=<domain-admin-email>                          │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                       │
│ Domain Name: <free-subdomain> or owned domain → points to VM IP     │
└───────────────────────────────────────────────────────────────────────┘
```

## Implementation Tasks

### Task 1: Provision Oracle Cloud "Always Free" VM

**Objective:** Set up a running Linux VM (Ubuntu or Oracle Linux) with Docker and Docker Compose pre-installed.

**Acceptance Criteria:**
- [ ] VM is provisioned in Oracle Cloud (free tier)
- [ ] VM has public IP assigned and is reachable via SSH
- [ ] VM runs Ubuntu 22.04 LTS or Oracle Linux 8+
- [ ] Docker and Docker Compose are installed (`docker --version`, `docker-compose --version`)
- [ ] VM has at least 2 GB RAM and 20 GB disk (sufficient for Postgres + Kafka + API)
- [ ] SSH key pair is generated and stored securely
- [ ] Firewall rules allow inbound traffic on ports 80 and 443 (HTTP/HTTPS) and 22 (SSH)
- [ ] Operator can SSH into the VM without issues

**Dependencies:** None  
**Estimated effort:** 30 minutes (provisioning) + 10 minutes (setup)

---

### Task 2: Create Backend Dockerfile

**Objective:** Build a Docker image that installs the JobHunt Python package and can run either the API server or consumer workers.

**Acceptance Criteria:**
- [ ] `Dockerfile` exists at repo root with `FROM python:3.11-slim`
- [ ] Dockerfile installs system dependencies (if any) and runs `pip install -e .`
- [ ] Dockerfile exposes port 8000 (for the API)
- [ ] Dockerfile has no hardcoded secrets or environment-dependent configs
- [ ] Image builds without errors: `docker build -t job-hunt-backend .`
- [ ] Image can run the API: `docker run -e DATABASE_URL=... job-hunt-backend python scripts/run_server.py`
- [ ] Image can run consumers: `docker run -e DATABASE_URL=... job-hunt-backend python scripts/run_consumers.py`
- [ ] Image is minimal in size (<500 MB preferred)

**Dependencies:** None  
**Estimated effort:** 45 minutes (writing, testing, optimizing)

---

### Task 3: Create docker-compose.prod.yml (Overlay File)

**Objective:** Define a production overlay for docker-compose that extends the existing local dev `docker-compose.yml` with `api`, `consumers`, and `caddy` services.

**Acceptance Criteria:**
- [ ] File `docker-compose.prod.yml` exists at repo root
- [ ] Overlay file extends (does not replace) the existing `docker-compose.yml`
- [ ] Defines `api` service:
  - Image: the newly-built backend image
  - Command: `python scripts/run_server.py`
  - Ports: `8000:8000` (internal only, Caddy proxies)
  - Depends on: `postgres`, `kafka`
  - Env vars: `DATABASE_URL`, `KAFKA_BOOTSTRAP_SERVERS`, `LLM_PROVIDER`, `GROQ_API_KEY`, `JWT_SECRET`, `CORS_ORIGINS`
- [ ] Defines `consumers` service:
  - Image: same backend image
  - Command: `python scripts/run_consumers.py`
  - No exposed ports (background worker)
  - Depends on: `postgres`, `kafka`
  - Same env vars as API
- [ ] Defines `caddy` service:
  - Image: `caddy:2` (latest stable)
  - Ports: `80:80`, `443:443`
  - Volumes: mounts `Caddyfile` (to be created in Task 4)
  - Depends on: `api`
- [ ] Can be started with: `docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d`
- [ ] All three services start, remain healthy, and restart on failure

**Dependencies:** Task 2 (Dockerfile)  
**Estimated effort:** 30 minutes

---

### Task 4: Create Caddy Reverse Proxy Configuration

**Objective:** Set up Caddy to terminate HTTPS with auto-renewed Let's Encrypt certificates and proxy traffic to the FastAPI API.

**Acceptance Criteria:**
- [ ] File `Caddyfile` exists at repo root (or in a config subdirectory)
- [ ] Caddyfile contains:
  - Directive: `api.<domain-name> { ... }` (domain placeholder)
  - `reverse_proxy localhost:8000` pointing to the API service
  - `tls <email>` for auto-renewal with Let's Encrypt (email placeholder)
  - No hardcoded secrets
- [ ] Caddy container reads the Caddyfile from a mounted volume
- [ ] Caddy successfully obtains a certificate on first startup (tested locally with a test domain or mock)
- [ ] HTTPS handshake succeeds: `curl https://api.<domain>/docs` returns 200
- [ ] Caddy auto-renews certs (no manual intervention required)
- [ ] Caddy logs are accessible via `docker compose logs caddy`

**Dependencies:** Task 3 (docker-compose.prod.yml)  
**Estimated effort:** 30 minutes

---

### Task 5: Acquire & Configure a Domain Name

**Objective:** Set up a domain (free or owned) that points to the VM's public IP and is used for HTTPS certs and API calls.

**Acceptance Criteria:**
- [ ] Domain or subdomain chosen (e.g., `api.yourproject.com` or free subdomain via DuckDNS/Cloudflare)
- [ ] DNS A record points to the VM's public IP
- [ ] DNS resolves correctly: `nslookup api.<domain>` returns the VM's IP
- [ ] Domain is used in Caddyfile directive
- [ ] Domain is used in `CORS_ORIGINS` in `.env.production`
- [ ] Domain is used in Vercel's `VITE_API_BASE_URL` env var

**Options:**
- **Free:** DuckDNS (duckdns.org) or Cloudflare (free tier with own domain)
- **Owned:** Register via any registrar; point nameservers or A record to VM IP

**Dependencies:** Task 1 (VM provisioned with public IP)  
**Estimated effort:** 15 minutes (if free) to 1 hour (if registering new domain)

---

### Task 6: Create .env.production and Secrets

**Objective:** Set up all environment variables needed for production, securely stored on the VM only.

**Acceptance Criteria:**
- [ ] File `.env.production` created on the VM (never committed to repo)
- [ ] `.env.production` contains:
  - `DATABASE_URL=postgresql://job_hunt_user:password@postgres:5432/job_hunt`
  - `KAFKA_BOOTSTRAP_SERVERS=kafka:9092`
  - `LLM_PROVIDER=groq`
  - `GROQ_API_KEY=<secret, obtained from Groq>` (note: Groq free tier available)
  - `JWT_SECRET=<generated random 32+ char string>`
  - `CORS_ORIGINS=https://vercel-app.vercel.app` (Vercel deployment URL)
  - `CADDY_EMAIL=<your-email>` (for Let's Encrypt notifications)
  - Other vars as needed (e.g., `PYTHONUNBUFFERED=1`)
- [ ] `.env.production` is added to `.gitignore` (never committed)
- [ ] Groq API key is obtained (free tier: https://console.groq.com)
- [ ] JWT_SECRET is generated randomly (e.g., `openssl rand -hex 32`)
- [ ] Postgres password is strong (randomly generated, e.g., `openssl rand -hex 16`)
- [ ] docker-compose.prod.yml references these env vars, and they are loaded on container startup
- [ ] No secrets are visible in Vercel build logs or frontend network requests

**Dependencies:** Task 5 (domain name), Task 3 (docker-compose.prod.yml)  
**Estimated effort:** 20 minutes (secret generation, Groq signup)

---

### Task 7: Configure Vercel Frontend Deployment

**Objective:** Deploy the React/Vite frontend to Vercel with the backend API URL configured.

**Acceptance Criteria:**
- [ ] Vercel project is created (or linked if already exists)
- [ ] Build command is set to: `cd frontend && npm run build`
- [ ] Output directory: `frontend/dist`
- [ ] Environment variable `VITE_API_BASE_URL=https://api.<domain>` is set in Vercel dashboard
- [ ] Deployment succeeds without errors
- [ ] Frontend loads at the Vercel URL (e.g., `https://my-project.vercel.app`)
- [ ] Frontend can make API calls to the backend URL (CORS headers verified)
- [ ] Sign-in form appears and is interactive

**Dependencies:** Task 5 (domain name), Task 6 (.env.production with CORS_ORIGINS)  
**Estimated effort:** 15 minutes

---

### Task 8: Deploy Backend Stack to VM

**Objective:** Copy the docker-compose stack to the VM and start all services.

**Acceptance Criteria:**
- [ ] Copy `docker-compose.yml`, `docker-compose.prod.yml`, `Dockerfile`, and `Caddyfile` to the VM
- [ ] Copy `.env.production` to the VM (manually, securely)
- [ ] SSH into VM and run: `docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d`
- [ ] All services start and remain running:
  - `docker-compose ps` shows 5 services (postgres, kafka, api, consumers, caddy) as `Up`
- [ ] Caddy successfully obtains HTTPS cert (check logs: `docker-compose logs caddy | grep "certificate obtained"`)
- [ ] API is accessible: `curl https://api.<domain>/health` returns 200
- [ ] Kafka consumers are subscribed (check logs: `docker-compose logs consumers | grep "subscribed"`)
- [ ] No errors in logs: `docker-compose logs` shows no critical errors

**Dependencies:** Tasks 2–7  
**Estimated effort:** 15 minutes (deployment) + 10 minutes (troubleshooting if needed)

---

### Task 9: Run Smoke Test (Playwright E2E Suite)

**Objective:** Run the existing Playwright test suite against the deployed instance to verify all features work.

**Acceptance Criteria:**
- [ ] Playwright e2e tests are located in: `frontend/tests/e2e/`
- [ ] Tests are configured to run against the deployed URL (not localhost)
- [ ] Run: `npm run test:e2e -- --base-url=https://vercel-app.vercel.app`
- [ ] All tests pass (100% pass rate)
- [ ] No intermittent failures or flakes
- [ ] Test output shows all major user flows validated (sign-in, profile creation, agent reasoning, etc.)
- [ ] Smoke test duration: <5 minutes total

**Dependencies:** Tasks 7–8 (frontend and backend deployed)  
**Estimated effort:** 10 minutes (run tests) + 30 minutes (debug if failures)

---

### Task 10: Manual Feature Walkthrough

**Objective:** Operator manually tests all locally-working features on the deployed instance.

**Acceptance Criteria:**
- [ ] Operator navigates to https://vercel-app.vercel.app
- [ ] Sign-in page loads and is responsive
- [ ] Operator signs in with valid credentials (or creates new account via sign-up)
- [ ] Dashboard/home page loads
- [ ] Profile page loads; operator can edit profile
- [ ] Job discovery/matching feature works; lists jobs
- [ ] Agent reasoning/chat feature works; can trigger analysis and see responses
- [ ] All API calls return data within <3 seconds (reasonable latency)
- [ ] No JavaScript errors in browser console
- [ ] Responsive design works on desktop and mobile

**Dependencies:** Task 9 (smoke tests passed)  
**Estimated effort:** 20 minutes

---

### Task 11: Reset Database for Beta Testers

**Objective:** Clear all test data and prepare a clean database for beta testers to use.

**Acceptance Criteria:**
- [ ] Script exists: `python scripts/reset_db.py`
- [ ] SSH into VM and run: `docker-compose exec api python scripts/reset_db.py`
- [ ] Script drops all application tables (users, profiles, jobs, matches, etc.)
- [ ] Script recreates all tables from schema/migrations
- [ ] Postgres is now at a clean state (ready for testers)
- [ ] No data from local testing remains
- [ ] Script completes without errors

**Dependencies:** Task 8 (backend deployed)  
**Estimated effort:** 5 minutes

---

### Task 12: Create Deployment Runbook

**Objective:** Document operational procedures for running and maintaining the deployed instance.

**Acceptance Criteria:**
- [ ] File `docs/deployment/RUNBOOK.md` created with sections:
  - **Quick start:** How to start/stop the stack
  - **Logs & debugging:** How to view logs, restart services
  - **Secrets & config:** Where `.env.production` lives, how to rotate secrets
  - **Database:** How to back up Postgres, how to reset DB
  - **Domain & HTTPS:** How Caddy works, when certs renew, what to do if renewal fails
  - **Monitoring:** What to watch for, how to know if something is wrong
  - **OS patching:** Reminder to regularly apply security updates to the VM
  - **Backup strategy:** Manual steps for backing up Postgres (or auto-backup via cron)
  - **Troubleshooting:** Common issues and solutions
- [ ] Runbook is clear enough that the operator can follow it without the spec
- [ ] Runbook references Tasks 1–11 as background
- [ ] Runbook is shared with beta testers (read-only, for reference)

**Dependencies:** Tasks 1–11  
**Estimated effort:** 1 hour

---

## Verification Checklist

**Before inviting beta testers, verify:**

- [ ] All 12 tasks are complete (Tasks 1–11 done, Task 12 documented)
- [ ] Smoke test (Task 9) passes 100%
- [ ] Manual feature walkthrough (Task 10) succeeds
- [ ] Database is reset and clean (Task 11)
- [ ] Runbook is documented and shared (Task 12)
- [ ] Operator is confident in operating the stack
- [ ] No secrets are visible in logs, Vercel, or frontend
- [ ] Domain name and HTTPS are working correctly
- [ ] VM has been tested with at least one full restart cycle
- [ ] Operator has SSH access and can troubleshoot if needed

---

## Timeline Estimate

- **Total effort:** ~5–7 hours (accounting for learning, testing, and debugging)
- **Parallelizable:** Tasks 1, 2, 5 can be started in parallel; most others are sequential
- **Critical path:** 1 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12

---

## Rollback Plan

If deployment fails critically:

1. **Before testers are invited:** Simply don't share the URL; re-run Task 8 (or roll back the VM to a previous snapshot if available)
2. **After testers are invited:** Inform testers of the issue, restore from a clean DB backup (Task 11), and restart the stack
3. **If VM is unrecoverable:** Provision a new VM (Task 1) and re-deploy the stack (Task 8)

For a beta-stage deployment at this scale, downtime of a few hours is acceptable; no production SLA is needed.

---

## References & Links

- Approved design: `docs/superpowers/specs/2026-09-02-beta-deployment-design.md`
- Product spec: `docs/deployment/PRODUCT.md`
- Deployment runbook: `docs/deployment/RUNBOOK.md` (to be created in Task 12)
