# AI-Powered Multi-Agent Career Opportunity Platform

An AI-powered, profession-independent career opportunity and networking
platform. It turns job searching from a pile of repetitive manual tasks
(reading postings, judging fit, finding referral contacts, writing outreach,
tracking status) into a structured, event-driven workflow — while keeping
external outreach under human approval.

The platform works for any profession (software, mechanical, HR, finance,
design, ...). It has no hard-coded assumptions about the user's role —
profiles are inferred dynamically from uploaded resumes.

See [about_project.md](about_project.md) for the full product spec (user
journeys, matching logic, contact ranking, outreach generation, lifecycle
states) and [docs/architecture/](docs/architecture/) for the technical
architecture.

## How it works, briefly

```text
Resume Upload / Job URL / Auto-Discovery
              │
              ▼
        Job Ingestion  ──publishes──▶  jobs.discovered (Kafka)
                                              │
                                              ▼
                                    LangGraph Matching Workflow
                                    (compares job against every
                                     candidate profile, picks the
                                     best-fit resume, scores match)
                                              │
                                              ▼
                                       jobs.shortlisted
                                          /          \
                              Contact Discovery     Tracker
                                          │
                                    Rank Contacts
                                          │
                                  Outreach Generation
                                          │
                                   Human Approval  ◀── you decide
                                          │
                                    Send Outreach
                                          │
                                       Tracker (source of truth)
```

- **LangGraph** owns reasoning/orchestration *inside* a component (e.g. one
  matching decision for one job).
- **Kafka** owns asynchronous, parallel work distribution *between*
  independently scalable components (many jobs across many workers).
- **PostgreSQL** owns all persistent state.
- **Ollama** provides local LLM inference (resume/job understanding, match
  scoring, outreach drafting).

## Technology stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| API | FastAPI + Uvicorn |
| Agent orchestration | LangGraph |
| Event streaming | Apache Kafka |
| Database | PostgreSQL + SQLAlchemy |
| Local LLM | Ollama |
| Job-page extraction | Playwright / BeautifulSoup |
| Frontend | React + TypeScript + Vite |
| Local infra | Docker Compose (Postgres, Kafka) |

## Prerequisites

Install these before running anything:

- **Python 3.11+**
- **Node.js** (18+) and **npm** — for the frontend
- **Docker Desktop** — runs Postgres and Kafka locally
- **[Ollama](https://ollama.com)** — runs natively on the host (not in
  Docker); pull a model after installing, e.g.:
  ```
  ollama pull llama3.2:1b
  ```

## First-time setup

Run these once, from the repo root.

**1. Configure environment variables**

```
cp .env.example .env
```

Defaults work out of the box, with one exception: if your machine already
has a native PostgreSQL bound to port 5432, `docker-compose.yml` maps
Postgres to host port **5433** instead — make sure `DATABASE_URL` in `.env`
matches (`...@localhost:5433/career_platform`) if you hit connection errors.
Set `OLLAMA_MODEL` to whatever model you pulled.

**2. Install the Python package (editable) and dev dependencies**

```
pip install -e ".[dev]"
```

**3. Install frontend dependencies**

```
cd frontend
npm install
cd ..
```

**4. Start Postgres and Kafka**

```
docker compose up -d
```

**5. Create the database tables**

```
python scripts/bootstrap_db.py
```

Safe to re-run — it only creates tables that don't already exist.

## Starting the application

Each of these runs in the foreground — use separate terminals (or background
them yourself). Start them in this order.

**1. Infrastructure** (if not already running)

```
docker compose up -d
```

**2. Backend API**

```
python scripts/run_server.py
```

Serves the FastAPI app at `http://localhost:8000`
(interactive docs at `http://localhost:8000/docs`).

> Windows note: use `scripts/run_server.py`, not a bare
> `uvicorn api.main:app` — `psycopg`'s async driver doesn't work on
> asyncio's default Windows event loop, and this script works around it.

**3. Kafka consumers**

```
python scripts/run_consumers.py
```

Runs the workers that actually drive the pipeline downstream of ingestion —
matching, contact discovery, outreach, and tracking. Without this running,
jobs published to Kafka just sit unprocessed.

**4. Frontend**

```
cd frontend
npm run dev
```

Serves the UI at `http://localhost:5173`.

**5. Ollama**

Make sure Ollama is running and serving on `http://localhost:11434` (it
usually runs as a background service once installed; if not, start it with
`ollama serve`).

Once all of the above are running, open **http://localhost:5173**.

## Running tests

```
pytest                          # backend
cd frontend && npm test         # frontend unit tests
cd frontend && npm run e2e      # frontend end-to-end tests
```

## Project layout

See [docs/architecture/repository-structure.md](docs/architecture/repository-structure.md)
and [docs/frontend/repository-structure.md](docs/frontend/repository-structure.md)
for how the codebase is organized.
