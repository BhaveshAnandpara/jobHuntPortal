# Backend image for the career-opportunity-platform (JobHunt) API and Kafka
# consumer workers. One image, two entrypoints:
#
#   docker run --rm job-hunt-backend python scripts/run_server.py     # FastAPI on :8000
#   docker run --rm job-hunt-backend python scripts/run_consumers.py  # Kafka consumers
#
# The image carries no configuration of its own: DATABASE_URL,
# KAFKA_BOOTSTRAP_SERVERS, LLM_PROVIDER, GROQ_API_KEY, JWT_SECRET,
# CORS_ALLOWED_ORIGINS, ... are all read from the process environment at
# runtime (see .env.example) and must be injected by the orchestrator.
# Nothing environment-specific and no secret is baked in here.
FROM python:3.11-slim

# PYTHONDONTWRITEBYTECODE: no .pyc files, keeps the layer smaller.
# PYTHONUNBUFFERED: log lines reach `docker logs` immediately.
# PIP_*: no wheel cache in the image, no version-check chatter in build logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# No *build* toolchain is installed here, and that is deliberate — every
# dependency in pyproject.toml ships a prebuilt manylinux cp311 wheel, so
# nothing is compiled from source at install time:
#   - confluent-kafka bundles librdkafka inside its wheel (no librdkafka-dev)
#   - psycopg[binary] bundles libpq inside psycopg-binary (no libpq-dev)
#   - bcrypt / pydantic-core ship compiled wheels (no build-essential/rustc)
# Adding a compiler toolchain here would cost hundreds of MB for nothing.
# There IS an apt-get step further down, but it is not for building Python
# packages — it installs Chromium's runtime shared libraries (see the
# `playwright install --with-deps` block below).

# Dependencies first, in their own layer: `pip install -e .` resolves
# packages from src/ (see [tool.setuptools.packages.find] where = ["src"]),
# so pyproject.toml and src/ must both be present for the editable install
# to register the component packages. scripts/ is copied afterwards so that
# entrypoint-only edits do not invalidate the dependency layer.
#
# `--no-compile` skips pip's byte-compilation step, which is worth a few
# hundred MB here (the LLM SDKs, playwright and their transitive deps are
# large, but their .pyc files are written all the same).
# The tradeoff: each container start recompiles the modules it actually
# imports instead of loading cached bytecode — a few seconds once, at
# startup, for two long-running processes. Caching the .pyc back would not
# help anyway, since PYTHONDONTWRITEBYTECODE=1 and the non-root runtime user
# cannot write to site-packages.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --no-compile -e .

COPY scripts ./scripts

# Chromium IS needed at runtime, so it is installed here.
#
# It is tempting to skip this: `playwright` is imported lazily, inside the
# method that uses it (src/infrastructure/external/page_fetch.py), so
# nothing touches a browser at import time. But laziness only defers the
# import — it does not mean the path is dead. The API container really does
# reach it:
#
#   scripts/run_server.py  ->  src/api/main.py
#     -> include_router(jobs/ingestion/api.py's router)
#     -> PageFetcherDependency = Annotated[PageFetcher, Depends(get_page_fetcher)]
#     -> jobs/ingestion/dependencies.py::get_page_fetcher() returns
#        PageFetchClient(StaticHttpRenderer(), fallback_renderer=PlaywrightPageRenderer())
#
# (jobs/discovery/dependencies.py re-exports the same factory, so the
# discovery routes share it.) PageFetchClient tries the cheap static HTTP
# fetch first and only escalates to PlaywrightPageRenderer when the static
# HTML fails `is_meaningful_content()` — i.e. when a job posting is an empty
# client-rendered SPA shell. That is not every request, but it is normal
# production traffic, and without a browser binary those postings fail
# outright, breaking the "job discovery/matching works; lists jobs" success
# criterion in docs/deployment/PRODUCT.md.
#
# `--with-deps` also apt-get installs the OS shared libraries Chromium
# needs, which python:3.11-slim does not ship. It must therefore run as
# root, before the USER switch below. PLAYWRIGHT_BROWSERS_PATH puts the
# browser in a shared location instead of root's home cache, so the non-root
# runtime user can actually read it; the ENV persists into the running
# container, where Playwright looks the browser up again at launch time.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Runtime-writable directories, created up front and owned by the non-root
# user: infrastructure/logging writes a rotating log under ./logs (LOG_DIR)
# and profiles/storage.py writes uploaded resumes under ./data/resumes
# (RESUME_STORAGE_DIR). Both are created by the app with mkdir(parents=True),
# which would fail against a root-owned /app once we drop privileges.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/logs /app/data/resumes \
    && chown -R appuser:appuser /app

USER appuser

# FastAPI listens on 0.0.0.0:8000 (see scripts/run_server.py). Behind Caddy
# in production this stays internal to the compose network.
EXPOSE 8000

# Default to the API server. Exec form, so docker-compose's `command:` (or a
# trailing `docker run` argument) cleanly overrides it with
# `python scripts/run_consumers.py` for the worker service.
CMD ["python", "scripts/run_server.py"]
