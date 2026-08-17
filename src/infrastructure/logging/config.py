"""Centralized application logging configuration.

One place owns handlers/formatters for the whole process — every other
module just calls `get_logger(__name__)` (a thin, named accessor over
stdlib `logging.getLogger`, matching this codebase's pre-existing
`logger = logging.getLogger(__name__)` convention already used across
several components) and logs through it normally. No module opens its own
file or attaches its own handler.

Log line shape (see `_FORMAT` below)::

    2026-08-15 01:12:43.218 | INFO | profile-service | Resume uploaded | resume_id=abc123 user_id=xyz789

- Timestamp: local system time (this project has no standardized UTC-only
  logging convention yet — domain *data* timestamps elsewhere in the
  codebase are UTC, but that's a separate concern from operator-facing log
  lines, which are more useful in local time when read directly off a
  developer machine).
- Level: standard `logging` levels — see `get_logger`'s docstring for how
  call sites should choose one.
- Service: a short, human-readable label derived from the logger's dotted
  module name (e.g. `profiles.service` -> `profile-service`) via
  `_service_name_for`, not the raw dotted path — see that function for the
  full mapping. Keeps every entry attributable at a glance without forcing
  every call site to pass its own service string.
- Message: the human-readable event text.
- Trailing context: an optional `key=value key2=value2` segment — build it
  with `format_context(**fields)` at the call site (see that function) and
  interpolate it into the message; there is no separate formatter field for
  it; on that failing.

Destinations: console (stderr) and a rotating file under `logs/` (created
relative to the current working directory — this project has no separate
"data dir" convention yet). Both share the exact same formatter, so a line
copied from the terminal matches the file byte-for-byte.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

_LOG_DIR = Path(os.environ.get("LOG_DIR", "logs"))
_LOG_FILE = _LOG_DIR / "application.log"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
_BACKUP_COUNT = 5  # application.log.1 .. application.log.5

_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)s | %(service)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Longest-matching dotted-prefix -> short human-readable service label.
# Ordered by nothing in particular — `_service_name_for` picks the longest
# matching prefix, so more-specific entries (e.g. "jobs.ingestion") always
# win over a shorter, more general one (e.g. "jobs") regardless of list
# order.
_SERVICE_NAME_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("users", "user-service"),
    ("profiles", "profile-service"),
    ("jobs.ingestion", "job-ingestion-service"),
    ("jobs.discovery", "job-discovery-service"),
    ("jobs", "job-service"),
    ("matching", "matching-service"),
    ("contacts", "contact-service"),
    ("outreach", "outreach-service"),
    ("tracking", "tracking-service"),
    ("workflows.langgraph.job_matching", "matching-workflow"),
    ("workflows.langgraph.contact_discovery", "contact-workflow"),
    ("workflows.langgraph.outreach_generation", "outreach-workflow"),
    ("workflows", "workflow"),
    ("infrastructure.kafka", "kafka"),
    ("infrastructure.database", "database"),
    ("infrastructure.llm", "llm-provider"),
    ("infrastructure.external", "external-integration"),
    ("api", "api"),
)

_NOISY_THIRD_PARTY_LOGGERS = ("httpx", "httpcore", "uvicorn.access")
"""Third-party loggers that default to WARNING regardless of the app's own
level, so their own INFO/DEBUG chatter doesn't drown out application log
lines. `uvicorn.access` is deliberately quieted here because
`api.main`'s own request-logging middleware (see that module) already
logs every request in this project's own format — keeping both would
double-log each request in two different shapes.
"""

_configured = False


def _service_name_for(logger_name: str) -> str:
    best_prefix = ""
    best_service = logger_name.split(".")[0]
    for prefix, service in _SERVICE_NAME_BY_PREFIX:
        matches = logger_name == prefix or logger_name.startswith(prefix + ".")
        if matches and len(prefix) > len(best_prefix):
            best_prefix = prefix
            best_service = service
    return best_service


class _ServiceNameFilter(logging.Filter):
    """Attaches `record.service` (see module docstring) so `_FORMAT`'s
    `%(service)s` always resolves, for every logger in the process."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = _service_name_for(record.name)
        return True


def configure_logging(
    level: int | str = logging.INFO, *, log_dir: Path | None = None, log_filename: str | None = None
) -> None:
    """Attach a console handler and a rotating file handler to the root
    logger. Idempotent — safe to call more than once in the same process
    (e.g. a test or script that imports `api.main` more than once); only
    the first call has any effect.

    `log_dir` defaults to the `LOG_DIR` environment variable (or `logs/`)
    when omitted — real callers never pass it. It exists as a parameter
    purely so `tests/infrastructure/test_logging.py` can point a real,
    fully-configured logger at an isolated `tmp_path` instead of the
    process's real environment/working directory.

    `log_filename` defaults to `application.log` (`_LOG_FILE.name`) when
    omitted. Every process's root logger is process-wide, so two OS
    processes sharing one file (the FastAPI server and the standalone
    Kafka consumer workers, per `scripts/run_server.py` and
    `scripts/run_consumers.py`) interleave their lines in one log with no
    way to `tail` just one side. `scripts/run_consumers.py` passes
    `"consumers.log"` here so a consumer-only incident (a wedged handler, a
    dead-lettered message, a `MAXPOLL`-triggered group departure) can be
    diagnosed from its own file without filtering out the API's request
    logs first.

    Deliberately NOT called at import time anywhere — see `api.main`'s
    lifespan handler for the one real call site. Importing this module (or
    any module that calls `get_logger`) never has the side effect of
    creating `logs/` or writing a file; only an actual app start does.
    """
    global _configured
    if _configured:
        return

    target_dir = log_dir if log_dir is not None else _LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target_filename = log_filename if log_filename is not None else _LOG_FILE.name

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
    service_filter = _ServiceNameFilter()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(service_filter)

    file_handler = logging.handlers.RotatingFileHandler(
        target_dir / target_filename,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(service_filter)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    for noisy_name in _NOISY_THIRD_PARTY_LOGGERS:
        logging.getLogger(noisy_name).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """`logger = get_logger(__name__)` — this project's logging call-site
    convention. Equivalent to stdlib `logging.getLogger(name)`; exists so
    call sites read consistently and so this module is the one place that
    could swap the underlying implementation later, without introducing a
    second logging system today.

    Use levels as follows:
      DEBUG    — detailed diagnostic detail, off by default in normal runs.
      INFO     — a normal, expected workflow event (upload received,
                 parsing completed, event published, ...).
      WARNING  — something unexpected that did NOT stop the operation
                 (a source's search failed and was skipped, a message was
                 routed to the DLQ, ...).
      ERROR    — an operation failed (a request 4xx/5xx'd for a reason
                 worth investigating, a handler raised).
      CRITICAL — reserved for failures that prevent the service/process
                 from operating at all; not used for ordinary request-level
                 failures.
    """
    return logging.getLogger(name)


def format_context(**fields: object) -> str:
    """Renders keyword fields as `key=value key2=value2` for a log line's
    trailing structured-context segment (see module docstring's example).
    `None` values are omitted so optional fields don't render as
    `key=None`; values containing whitespace are quoted so the line stays
    cleanly greppable field-by-field.

    Never pass full resume/outreach-message/prompt text here — see
    `about_project.md`'s and this change's security requirements. Pass
    IDs, statuses, counts, scores, and short error strings only.
    """
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value)
        if " " in text:
            text = f'"{text}"'
        parts.append(f"{key}={text}")
    return " ".join(parts)


__all__ = ["configure_logging", "format_context", "get_logger"]
