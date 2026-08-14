"""FastAPI app assembly — mounts each component's api/ router.
See docs/architecture/repository-structure.md and
docs/architecture/api-contracts.md.

Each router is defined and owned by its component (users/api/,
profiles/api/, jobs/ingestion/api.py, jobs/discovery/api.py,
matching/api/, contacts/api/, outreach/api/, tracking/api/) — this module
only wires them together. No route or business logic is defined here.

Also owns the one process-wide `configure_logging()` call (via the
`lifespan` context manager below, so it only fires for a real ASGI
lifespan — `uvicorn`, or `e2e/backend_server.py`'s own real `uvicorn`
process — never for a bare `TestClient(app)` construction, which is how
this project's test suite builds `app` without triggering startup/shutdown
events) and a lightweight request-logging middleware. See
`infrastructure/logging/config.py` for the logging design.
"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request

from contacts.api import router as contacts_router
from infrastructure.logging import configure_logging, format_context, get_logger
from jobs.discovery.api import router as job_discovery_router
from jobs.ingestion.api import router as job_ingestion_router
from matching.api import router as matching_router
from outreach.api import router as outreach_router
from profiles.api import router as profiles_router
from tracking.api import router as tracking_router
from users.api import router as users_router

logger = get_logger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info("Application starting | title=%r", _app.title)
    yield
    logger.info("Application shutting down")


app = FastAPI(
    title="AI-Powered Multi-Agent Career Opportunity Platform",
    lifespan=_lifespan,
)

app.include_router(users_router)
app.include_router(profiles_router)
app.include_router(job_ingestion_router)
app.include_router(job_discovery_router)
app.include_router(matching_router)
app.include_router(contacts_router)
app.include_router(outreach_router)
app.include_router(tracking_router)


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    """Lightweight per-request log line — method, path, status, duration,
    and a per-request correlation id (not the domain `CorrelationId` used
    for Kafka causal chains; this one only identifies one HTTP request/
    response pair in the log, which is enough to pair a request line with
    whatever component-level log lines a route handler emits during the
    same request). Never logs headers, query strings, or body content —
    see this change's security requirements.
    """
    request_id = uuid4().hex[:12]
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    logger.info(
        "%s %s | %s",
        request.method,
        request.url.path,
        format_context(status=response.status_code, duration_ms=duration_ms, request_id=request_id),
    )
    return response
