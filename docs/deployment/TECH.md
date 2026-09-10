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
│  APP VM — Oracle "Always Free" VM.Standard.E2.1.Micro (1 GB RAM)    │
│  Public IP: the domain's A record points HERE. Private IP: 10.0.0.b │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ docker compose -f docker-compose.app.yml (standalone):       │   │
│  │  ┌─────────────┐                                             │   │
│  │  │ Caddy       │  (ports 443/80, reverse proxy, HTTPS)       │   │
│  │  └──────┬──────┘                                             │   │
│  │         │ api:8000  (container-to-container, same VM)        │   │
│  │  ┌──────▼──────┐          ┌──────────────────┐               │   │
│  │  │ FastAPI API │          │ Groq API (ext)   │               │   │
│  │  │  (port 8000)│          │ (HTTPS egress)   │               │   │
│  │  └─────────────┘          └──────────────────┘               │   │
│  │  ┌────────────────────────┐                                  │   │
│  │  │ Consumer workers       │                                  │   │
│  │  │ (python scripts/...)   │                                  │   │
│  │  └────────────────────────┘                                  │   │
│  │                                                              │   │
│  │ Config: .env.production on THIS VM (never committed)         │   │
│  │  - DATA_VM_PRIVATE_IP=10.0.0.a  (the DATA VM's IP, not this) │   │
│  │  - DATABASE_URL=postgresql+psycopg://postgres:<pw>@          │   │
│  │        10.0.0.a:5432/career_platform                         │   │
│  │  - KAFKA_BOOTSTRAP_SERVERS=10.0.0.a:9092                     │   │
│  │  - LLM_PROVIDER=groq / GROQ_API_KEY=<secret>                 │   │
│  │  - JWT_SECRET_KEY=<secret>                                   │   │
│  │  - CORS_ALLOWED_ORIGINS=https://vercel-app-url.vercel.app    │   │
│  │  - API_DOMAIN=api.<domain> / CADDY_EMAIL=<admin-email>       │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────┬──────────────────────────────────────────┘
      Private VCN traffic  │ 10.0.0.0/24 subnet, ports 5432 + 9092
      (never over the public internet; Security List rule, Task 1)
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  DATA VM — Oracle "Always Free" VM.Standard.E2.1.Micro (1 GB RAM)   │
│  Private IP: 10.0.0.a. Its public IP is for SSH only — 5432 and     │
│  9092 are NOT open to the internet.                                 │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ docker compose -f docker-compose.data.yml (standalone):      │   │
│  │  ┌──────────────┐   ┌──────────────────────────────┐         │   │
│  │  │ Postgres     │   │ Kafka broker (KRaft)         │         │   │
│  │  │ (port 5432)  │   │ (port 9092, heap 384 MB)     │         │   │
│  │  │              │   │ topics: job_matches, ...     │         │   │
│  │  └──────────────┘   └──────────────────────────────┘         │   │
│  │  Kafka advertises PLAINTEXT://10.0.0.a:9092 — the PRIVATE    │   │
│  │  IP, because its clients live on the OTHER VM (see Task 3).  │   │
│  │                                                              │   │
│  │ Config: .env.production on THIS VM (never committed)         │   │
│  │  - POSTGRES_PASSWORD=<secret, matches the App VM's URL>      │   │
│  │  - DATA_VM_PRIVATE_IP=10.0.0.a   (this VM's own private IP)  │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘

Domain Name: <free-subdomain> or owned domain → points to the APP VM's
public IP (the Data VM is never named in DNS).
```

**Why two VMs.** The original plan was a single Always Free VM running the whole
stack. Oracle could not allocate the 24 GB ARM shape (`VM.Standard.A1.Flex`) in
this account's region — persistent out-of-capacity errors — so the deployment
uses the AMD alternative instead: two `VM.Standard.E2.1.Micro` instances at
1 GB RAM each. Two is the Always Free ceiling for that shape, not a first step
toward a larger cluster. Splitting stateful services (Postgres, Kafka) onto one
box and stateless ones (API, consumers, Caddy) onto the other is what makes
1 GB per box workable.

## Implementation Tasks

### Agent Assignment Summary

Most of these tasks touch production credentials, a live VM, or a real database — so the default is operator-executed, with an agent drafting the artifact or reviewing it first. Only the doc/config-authoring tasks and the automated test run are agent-led end to end.

| Task | Suggested Agent | Why |
|---|---|---|
| 1. Provision the two VMs + VCN rules | Operator (manual) | Requires Oracle Cloud console/account access |
| 2. Backend Dockerfile | general-purpose (draft) → ecc:security-reviewer (check) | Authoring is safe to delegate; review for leaked secrets/bad base image before use |
| 3. docker-compose.data.yml + docker-compose.app.yml | general-purpose (draft) → ecc:security-reviewer (check) | Same — config authoring plus a security pass |
| 4. Caddyfile | general-purpose | Static config file, no secrets, low risk |
| 5. Domain & DNS | Operator (manual) | Requires registrar/DNS provider account access |
| 6. .env.production & secrets | Operator (manual) | Secrets must never be generated/handled by an agent |
| 7. Vercel frontend config | general-purpose (build settings) → Operator (env var entry in dashboard) | Dashboard secret entry stays human-only |
| 8. Deploy stack to both VMs | Operator (manual, agent-assisted) | Live prod deploy over SSH — human-in-the-loop per risk policy |
| 9. Playwright smoke test | ecc:e2e-runner | Purpose-built for running/maintaining the e2e suite |
| 10. Manual feature walkthrough | Operator (manual) | Task is explicitly a human walkthrough |
| 11. Reset beta database | Operator (manual, agent-assisted) | Destructive prod DB action — python-reviewer can review `reset_db.py` beforehand, but execution stays human |
| 12. Deployment runbook | ecc:doc-updater | Documentation specialist |

### Task 1: Provision Two Oracle Cloud "Always Free" VMs

**Objective:** Set up **two** Linux VMs in the same VCN/subnet, each with Docker and Docker Compose installed: a **Data VM** (Postgres + Kafka) and an **App VM** (API + consumers + Caddy, internet-facing).

**Shape:** `VM.Standard.E2.1.Micro` (AMD, 1 GB RAM each) ×2. The Always Free tier allows exactly two of these — this is the ceiling. The 24 GB ARM shape `VM.Standard.A1.Flex` was the original plan but is out of capacity in this region; do not wait on it.

**Acceptance Criteria:**
- [ ] **Two** VMs are provisioned in Oracle Cloud (free tier), both `VM.Standard.E2.1.Micro`
- [ ] Both VMs are in the **same VCN and the same subnet** (private range `10.0.0.0/24`)
- [ ] Both VMs run Ubuntu 22.04 LTS or Oracle Linux 8+
- [ ] Both VMs have a public IP and are reachable via SSH; the operator can SSH into each
- [ ] Docker and Docker Compose are installed on both (`docker --version`, `docker compose version`)
- [ ] Each VM has ≥ 20 GB disk (default free-tier boot volume is ample)
- [ ] **Both private IPs are written down** — e.g. Data VM `10.0.0.a`, App VM `10.0.0.b`. Every later task needs the Data VM's private IP; nothing in the repo hardcodes it
- [ ] The two VMs can reach each other on the private network: from the App VM, `ping 10.0.0.a` succeeds
- [ ] SSH key pair(s) generated and stored securely (one key may be reused for both VMs)
- [ ] Security List allows inbound `22` (SSH) on both VMs, and `80` + `443` (HTTP/HTTPS) on the **App VM**
- [ ] **NEW — this topology only:** a Security List ingress rule allows source CIDR `10.0.0.0/24` (or, tighter, the App VM's private IP `/32`) to reach the **Data VM** on TCP **5432** and **9092**
- [ ] `5432` and `9092` are **not** reachable from the public internet — verify from your laptop: `nc -zv <data-vm-PUBLIC-ip> 5432` must fail
- [ ] The Data VM's host firewall does not block the above. Oracle's Ubuntu images ship restrictive `iptables` rules that are easy to miss — if the private-network check fails while the Security List looks right, that is almost always why (`sudo iptables -L INPUT -n --line-numbers`)
- [ ] **Recommended on both VMs:** a 2–4 GB swapfile (see the RAM risk note in `PRODUCT.md`) — `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`, then add it to `/etc/fstab` so it survives reboot

**Why the new Security List rule:** on the single-VM plan, Postgres and Kafka only ever talked to containers on the same Docker network, so no port of theirs ever crossed a network boundary and no firewall rule was needed. Split across two VMs, those two connections become ordinary TCP traffic between hosts. Without this rule the App VM's containers start fine and then fail every database query and every Kafka poll — the single most likely thing to go wrong in this topology.

**Dependencies:** None  
**Estimated effort:** 45 minutes (provisioning both) + 15 minutes (networking check, swap)  
**Suggested agent:** Operator (manual) — requires Oracle Cloud console/account access

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
**Suggested agent:** general-purpose (draft) → ecc:security-reviewer (check for secrets/insecure base image)

---

### Task 3: Create docker-compose.data.yml and docker-compose.app.yml

**Objective:** Define one standalone compose file per VM. There is no production *overlay* any more: the local-dev `docker-compose.yml` stays untouched and is never passed with `-f` on either VM, because two of its settings (host port `5433`, and Kafka advertising `localhost`) are actively wrong in production.

**Acceptance Criteria:**

- [ ] File `docker-compose.data.yml` exists at repo root — **Data VM only**, standalone (never combined with `docker-compose.yml`)
  - `postgres` (`postgres:16`): `POSTGRES_USER=postgres`, `POSTGRES_DB=career_platform`, and `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?...}` — the `:?` required-variable form, never a default that could silently fall back to the dev password
  - `postgres` **publishes** `5432:5432` to the VM host (not merely `expose`) — the client is on another machine, so there is no shared Docker network to reach it over. The dev file's `5433:5432` conflict-avoidance mapping is dropped; a fresh cloud VM has no native Postgres to collide with
  - `kafka` (`apache/kafka:3.7.0`, KRaft) **publishes** `9092:9092` for the same reason
  - `KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://${DATA_VM_PRIVATE_IP:?...}:9092` — see the box below
  - `KAFKA_HEAP_OPTS: "-Xmx384m -Xms384m"` — the image otherwise defaults to `-Xmx1G -Xms1G`, i.e. the VM's entire RAM, alongside Postgres
  - `KAFKA_CONTROLLER_QUORUM_VOTERS: 0@localhost:9093` — controller traffic stays inside the container and must **not** use the private IP
  - Named volumes `postgres-data` / `kafka-data`; `restart: unless-stopped` on both; `pg_isready` healthcheck kept on Postgres
  - Starts with: `docker compose --env-file .env.production -f docker-compose.data.yml up -d`

> **The advertised-listener trap — read before editing the kafka block.**
> A Kafka client does not keep talking to the address it bootstrapped against. Bootstrap only fetches metadata; the broker replies with its *advertised* address and the client reconnects to that. An unreachable advertised address therefore fails in a very confusing way: bootstrap succeeds, then every produce and consume fails.
> This already bit the single-VM version once, where the dev file's `localhost:9092` made the API container resolve `localhost` to *itself*; the fix then was to advertise the compose service name `kafka`. **That fix is wrong here too**, in a new form — there is no shared Docker network across two VMs, so the name `kafka` resolves to nothing on the App VM. The only address that works for every client is the Data VM's **private IP**, supplied from `.env.production` and never hardcoded.

- [ ] File `docker-compose.app.yml` exists at repo root — **App VM only**, standalone
  - `api`: `build: .`, `command: python scripts/run_server.py`, `expose: 8000` (never published; Caddy proxies), `env_file: .env.production`, `restart: unless-stopped`
  - `consumers`: same build, `command: python scripts/run_consumers.py`, no ports, `env_file: .env.production`, `restart: unless-stopped`
  - `caddy` (`caddy:2`): ports `80:80` + `443:443`, mounts `./Caddyfile` read-only, named volumes `caddy_data` / `caddy_config`, receives `API_DOMAIN` and `CADDY_EMAIL` by interpolation only (never the whole secrets file)
  - `DATABASE_URL` and `KAFKA_BOOTSTRAP_SERVERS` point at the **Data VM's private IP** — see Task 6 for the exact strings. `postgres:5432` / `kafka:9092` from the old single-VM stack resolve to nothing here
  - `caddy` depends on `api` with `service_started` (not `service_healthy`): if the Data VM is down, a Caddy that refuses to start would take the site fully dark instead of returning a diagnosable 502
  - Starts with: `docker compose --env-file .env.production -f docker-compose.app.yml up -d`

- [ ] **Known and accepted loss:** `api`/`consumers` can no longer use `depends_on: postgres: {condition: service_healthy}`. Docker Compose has no cross-*host* dependency or health condition — it only orders containers in its own project on its own machine. Replaced by (a) deploy order (Task 8: Data VM first), (b) `restart: unless-stopped` plus the app's own connection retry logic, and (c) a `GET /health` healthcheck on `api`, which reports Postgres and Kafka reachability from the client side and surfaces "Data VM unreachable" as `unhealthy` in `docker compose ps`
- [ ] Both files parse: `docker compose -f docker-compose.data.yml config` and `docker compose -f docker-compose.app.yml config` (with a throwaway `.env.production`, never a real one)
- [ ] The Caddyfile needs **no change** for the split — it only references `api:8000`, still a container beside Caddy on the App VM

**Dependencies:** Task 2 (Dockerfile)  
**Estimated effort:** 45 minutes  
**Suggested agent:** general-purpose (draft) → ecc:security-reviewer (check)

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

**Dependencies:** Task 3 (`docker-compose.app.yml`, which mounts this file)  
**Estimated effort:** 30 minutes  
**Suggested agent:** general-purpose — static config, no secrets involved

**Unaffected by the two-VM split:** Caddy and the API are still containers side by side on the App VM, so `reverse_proxy api:8000` remains correct and the Caddyfile needs no change.

---

### Task 5: Acquire & Configure a Domain Name

**Objective:** Set up a domain (free or owned) that points to the **App VM's** public IP and is used for HTTPS certs and API calls. The Data VM is never named in DNS — it is reached only over the private subnet.

**Acceptance Criteria:**
- [ ] Domain or subdomain chosen (e.g., `api.yourproject.com` or free subdomain via DuckDNS/Cloudflare)
- [ ] DNS A record points to the **App VM's** public IP (the VM running Caddy)
- [ ] DNS resolves correctly: `nslookup api.<domain>` returns the App VM's IP
- [ ] Domain is used in Caddyfile directive
- [ ] Domain is used in `CORS_ALLOWED_ORIGINS` in `.env.production`
- [ ] Domain is used in Vercel's `VITE_API_BASE_URL` env var

**Options:**
- **Free:** DuckDNS (duckdns.org) or Cloudflare (free tier with own domain)
- **Owned:** Register via any registrar; point nameservers or A record to VM IP

**Dependencies:** Task 1 (VM provisioned with public IP)  
**Estimated effort:** 15 minutes (if free) to 1 hour (if registering new domain)  
**Suggested agent:** Operator (manual) — requires registrar/DNS provider account access

---

### Task 6: Create .env.production and Secrets

**Objective:** Set up all environment variables needed for production, securely stored on the VMs only. **There are now two `.env.production` files — one per VM, with different contents.** Neither is ever committed.

**Acceptance Criteria:**

- [ ] File `.env.production` created on the **Data VM** (repo root there), containing exactly:
  - `POSTGRES_PASSWORD=<generated, e.g. openssl rand -hex 16>`
  - `DATA_VM_PRIVATE_IP=10.0.0.a` — *this* VM's own private IP, from Task 1
- [ ] File `.env.production` created on the **App VM** (repo root there), containing:
  - `DATA_VM_PRIVATE_IP=10.0.0.a` — the **Data VM's** private IP, not this VM's
  - `DATABASE_URL=postgresql+psycopg://postgres:<POSTGRES_PASSWORD>@${DATA_VM_PRIVATE_IP}:5432/career_platform`
  - `KAFKA_BOOTSTRAP_SERVERS=${DATA_VM_PRIVATE_IP}:9092`
  - `LLM_PROVIDER=groq`
  - `GROQ_API_KEY=<secret, obtained from Groq>` (note: Groq free tier available)
  - `GROQ_MODEL=openai/gpt-oss-120b` (optional — has a default)
  - `JWT_SECRET_KEY=<generated random 32+ char string>`
  - `CORS_ALLOWED_ORIGINS=https://vercel-app.vercel.app` (Vercel deployment URL)
  - `API_DOMAIN=api.<domain>` (read by the Caddyfile)
  - `CADDY_EMAIL=<your-email>` (for Let's Encrypt notifications)
  - Other vars as needed (e.g., `PYTHONUNBUFFERED=1`)
- [ ] The password inside the App VM's `DATABASE_URL` **matches** `POSTGRES_PASSWORD` on the Data VM character for character — a mismatch is authentication failures on every request, with a healthy-looking database
- [ ] `DATABASE_URL` and `KAFKA_BOOTSTRAP_SERVERS` use the Data VM's **private** IP. Not `postgres:5432` / `kafka:9092` (Docker service names, which do not exist across VMs), not `localhost` (the App VM itself), not the Data VM's public IP (those ports are firewalled off the internet, and should stay that way)
- [ ] `.env.production` is in `.gitignore` (already is) and is never committed from either VM
- [ ] Groq API key is obtained (free tier: https://console.groq.com)
- [ ] JWT_SECRET_KEY is generated randomly (e.g., `openssl rand -hex 32`)
- [ ] Postgres password is strong (randomly generated, e.g., `openssl rand -hex 16`)
- [ ] Both compose files are always run with `--env-file .env.production`; each aborts with an explanatory message if its required variables are missing
- [ ] No secrets are visible in Vercel build logs or frontend network requests

**Dependencies:** Task 1 (both private IPs), Task 5 (domain name), Task 3 (compose files)  
**Estimated effort:** 20 minutes (secret generation, Groq signup)  
**Suggested agent:** Operator (manual) — secrets must never be generated or handled by an agent

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

**Dependencies:** Task 5 (domain name), Task 6 (.env.production with CORS_ALLOWED_ORIGINS)  
**Estimated effort:** 15 minutes  
**Suggested agent:** general-purpose (build settings) → Operator (env var entry in Vercel dashboard)

---

### Task 8: Deploy Backend Stack to Both VMs

**Objective:** Deploy to the two VMs **in order — Data VM first, App VM second**. The order is not cosmetic: nothing on the App VM can connect to a database and broker that are not up yet, and Compose cannot express that dependency across hosts (see Task 3), so the operator supplies the ordering by hand.

**Step 1 — Data VM (do this first):**
- [ ] Copy `docker-compose.data.yml` to the Data VM (the repo checkout, or just that one file — it references no other repo file)
- [ ] Create `.env.production` on the Data VM per Task 6 (`POSTGRES_PASSWORD`, `DATA_VM_PRIVATE_IP`)
- [ ] SSH in and run: `docker compose --env-file .env.production -f docker-compose.data.yml up -d`
- [ ] `docker compose -f docker-compose.data.yml ps` shows **2** services (postgres, kafka) as `Up`, postgres `(healthy)`
- [ ] Kafka is advertising the private IP, not `localhost`: `docker compose -f docker-compose.data.yml logs kafka | grep advertised.listeners` shows `PLAINTEXT://10.0.0.a:9092`
- [ ] Kafka's heap cap took effect: `docker compose -f docker-compose.data.yml exec kafka sh -c 'tr "\0" " " < /proc/1/cmdline' | grep -o -- "-Xmx[^ ]*"` prints `-Xmx384m`

**Step 2 — reachability check from the App VM (before deploying anything there):**
- [ ] `nc -zv 10.0.0.a 5432` succeeds
- [ ] `nc -zv 10.0.0.a 9092` succeeds
- [ ] If either fails, fix the Task 1 Security List rule / host `iptables` **now** — do not deploy on top of a broken network path and try to read it back out of application logs

**Step 3 — App VM:**
- [ ] Copy `docker-compose.app.yml`, `Dockerfile`, `Caddyfile`, `pyproject.toml`, `src/` and `scripts/` to the App VM (the `api`/`consumers` images are built there from this repo)
- [ ] Create `.env.production` on the App VM per Task 6 (Data VM private IP inside `DATABASE_URL` / `KAFKA_BOOTSTRAP_SERVERS`)
- [ ] SSH in and run: `docker compose --env-file .env.production -f docker-compose.app.yml up -d --build`
- [ ] `docker compose -f docker-compose.app.yml ps` shows **3** services (api, consumers, caddy) as `Up`, and `api` reaching `(healthy)` within ~2 minutes — `api` going `unhealthy` means it cannot reach the Data VM, so go back to Step 2
- [ ] Caddy successfully obtains HTTPS cert (check logs: `docker compose -f docker-compose.app.yml logs caddy | grep -i "certificate obtained"`)
- [ ] API is accessible end to end: `curl https://api.<domain>/health` returns **200** with `{"status":"ok","database":true,"kafka":true}` — both booleans `true` is the real cross-VM proof, since that endpoint probes Postgres and Kafka from inside the API container
- [ ] Kafka consumers are subscribed (check logs: `docker compose -f docker-compose.app.yml logs consumers | grep "subscribed"`)
- [ ] No errors in logs on either VM

**Restart order after a reboot:** same as above — Data VM first. `restart: unless-stopped` brings containers back automatically on each VM independently, and the App VM's services will retry until the Data VM answers, so an out-of-order reboot self-heals rather than requiring intervention.

**Dependencies:** Tasks 2–7  
**Estimated effort:** 30 minutes (deployment across both VMs) + 15 minutes (troubleshooting if needed)  
**Suggested agent:** Operator (manual, agent-assisted) — live prod deploy over SSH, human-in-the-loop

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
**Suggested agent:** ecc:e2e-runner

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
**Suggested agent:** Operator (manual) — this task is explicitly a human walkthrough

---

### Task 11: Reset Database for Beta Testers

**Objective:** Clear all test data and prepare a clean database for beta testers to use.

**Acceptance Criteria:**
- [ ] Script exists: `python scripts/reset_db.py`
- [ ] SSH into the **App VM** and run: `docker compose -f docker-compose.app.yml exec api python scripts/reset_db.py` — the script runs in the API container (which holds `DATABASE_URL`) and reaches the Data VM's Postgres over the private network; there is no need to SSH into the Data VM for this
- [ ] Script drops all application tables (users, profiles, jobs, matches, etc.)
- [ ] Script recreates all tables from schema/migrations
- [ ] Postgres is now at a clean state (ready for testers)
- [ ] No data from local testing remains
- [ ] Script completes without errors

**Dependencies:** Task 8 (backend deployed)  
**Estimated effort:** 5 minutes  
**Suggested agent:** Operator (manual, agent-assisted) — destructive prod DB action; ecc:python-reviewer can review `reset_db.py` beforehand, execution stays human

---

### Task 12: Create Deployment Runbook

**Objective:** Document operational procedures for running and maintaining the deployed instance.

**Acceptance Criteria:**
- [ ] File `docs/deployment/RUNBOOK.md` created with sections:
  - **Quick start:** How to start/stop each VM's stack, and the required order (Data VM before App VM)
  - **Logs & debugging:** How to view logs, restart services
  - **Secrets & config:** Where `.env.production` lives, how to rotate secrets
  - **Database:** How to back up Postgres, how to reset DB
  - **Domain & HTTPS:** How Caddy works, when certs renew, what to do if renewal fails
  - **Monitoring:** What to watch for, how to know if something is wrong
  - **OS patching:** Reminder to regularly apply security updates to **both** VMs
  - **Two-VM specifics:** Which service runs where, the private IPs and where they are configured, and the "advertised listener / private IP" failure mode from Task 3
  - **Backup strategy:** Manual steps for backing up Postgres (or auto-backup via cron)
  - **Troubleshooting:** Common issues and solutions
- [ ] Runbook is clear enough that the operator can follow it without the spec
- [ ] Runbook references Tasks 1–11 as background
- [ ] Runbook is shared with beta testers (read-only, for reference)

**Dependencies:** Tasks 1–11  
**Estimated effort:** 1 hour  
**Suggested agent:** ecc:doc-updater

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
- [ ] **Both** VMs have been tested with at least one full restart cycle, including a reboot of the Data VM alone (the App VM must recover on its own once the Data VM is back)
- [ ] Operator has SSH access to **both** VMs and can troubleshoot if needed
- [ ] Ports 5432 and 9092 are confirmed unreachable from the public internet

---

## Timeline Estimate

- **Total effort:** ~6–8 hours (accounting for learning, testing, and debugging; the two-VM split adds roughly an hour of provisioning and networking over the original single-VM plan)
- **Parallelizable:** Tasks 1, 2, 5 can be started in parallel; most others are sequential. Within Task 8 the two VMs are *not* parallelizable — Data VM first
- **Critical path:** 1 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12

---

## Rollback Plan

If deployment fails critically:

1. **Before testers are invited:** Simply don't share the URL; re-run Task 8 (or roll back the affected VM to a previous snapshot if available)
2. **After testers are invited:** Inform testers of the issue, restore from a clean DB backup (Task 11), and restart the stack
3. **If the App VM is unrecoverable:** Re-provision it (Task 1) and re-deploy `docker-compose.app.yml` (Task 8, Step 3). The Data VM and all data are untouched; only the DNS A record needs re-pointing at the new public IP
4. **If the Data VM is unrecoverable:** Re-provision it (Task 1) and re-deploy `docker-compose.data.yml` (Task 8, Step 1). This loses the database unless a backup exists, and the new private IP must be written into **both** `.env.production` files before the App VM will work again

For a beta-stage deployment at this scale, downtime of a few hours is acceptable; no production SLA is needed.

---

## References & Links

- Approved design: `docs/superpowers/specs/2026-09-02-beta-deployment-design.md`
- Product spec: `docs/deployment/PRODUCT.md`
- Deployment runbook: `docs/deployment/RUNBOOK.md` (to be created in Task 12)
