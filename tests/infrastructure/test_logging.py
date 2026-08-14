"""Tests for `infrastructure/logging` — the centralized logging config
module added in this change. See `src/infrastructure/logging/config.py`'s
module docstring for the design this verifies.

Every test resets the module's `_configured` flag and removes any handlers
it attached, so `configure_logging` (which is deliberately idempotent
process-wide, matching real usage from `api.main`'s lifespan) doesn't leak
state into other tests in the same pytest session.
"""

from __future__ import annotations

import logging
import re

import pytest

from infrastructure.logging import config as logging_config
from infrastructure.logging import configure_logging, format_context, get_logger


@pytest.fixture(autouse=True)
def _reset_logging_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(logging_config, "_configured", False)
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    yield
    for handler in list(root.handlers):
        if handler not in original_handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(original_level)


def test_configure_logging_creates_the_log_directory_and_file(tmp_path):
    log_dir = tmp_path / "logs"
    assert not log_dir.exists()

    configure_logging(log_dir=log_dir)
    get_logger(__name__).info("boot")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert log_dir.is_dir()
    assert (log_dir / "application.log").is_file()


def test_configure_logging_is_idempotent_and_does_not_duplicate_handlers(tmp_path):
    configure_logging(log_dir=tmp_path)
    handler_count_after_first_call = len(logging.getLogger().handlers)

    configure_logging(log_dir=tmp_path / "second-call-should-be-ignored")

    assert len(logging.getLogger().handlers) == handler_count_after_first_call


def test_log_line_contains_timestamp_level_service_message_and_context(tmp_path):
    configure_logging(log_dir=tmp_path)
    logger = get_logger("profiles.service")

    logger.info("Resume uploaded | %s", format_context(resume_id="abc123", user_id="xyz789"))
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = (tmp_path / "application.log").read_text(encoding="utf-8")
    line = content.strip().splitlines()[-1]

    # TIMESTAMP: "YYYY-MM-DD HH:MM:SS.mmm"
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}", line)
    # LEVEL
    assert " | INFO | " in line
    # SERVICE — derived from the "profiles.service" logger name, not the raw dotted path.
    assert " | profile-service | " in line
    # MESSAGE
    assert "Resume uploaded" in line
    # RELEVANT IDS / CONTEXT
    assert "resume_id=abc123" in line
    assert "user_id=xyz789" in line


@pytest.mark.parametrize(
    ("logger_name", "expected_service"),
    [
        ("profiles.service", "profile-service"),
        ("profiles.api.routes", "profile-service"),
        ("jobs.ingestion.api", "job-ingestion-service"),
        ("jobs.discovery.service", "job-discovery-service"),
        ("jobs.repository", "job-service"),
        ("matching.consumers", "matching-service"),
        ("contacts.consumers", "contact-service"),
        ("outreach.consumers", "outreach-service"),
        ("outreach.api.routes", "outreach-service"),
        ("tracking.consumers", "tracking-service"),
        ("workflows.langgraph.job_matching.nodes", "matching-workflow"),
        ("workflows.langgraph.outreach_generation.graph", "outreach-workflow"),
        ("infrastructure.kafka.producer", "kafka"),
        ("infrastructure.database.engine", "database"),
        ("infrastructure.llm.ollama_provider", "llm-provider"),
        ("api.main", "api"),
    ],
)
def test_service_name_derivation_uses_longest_matching_prefix(logger_name, expected_service):
    assert logging_config._service_name_for(logger_name) == expected_service


def test_every_log_level_is_usable_and_reaches_the_file(tmp_path):
    configure_logging(log_dir=tmp_path)
    logger = get_logger("outreach.consumers")

    logger.debug("debug event")
    logger.info("info event")
    logger.warning("warning event")
    logger.error("error event")
    logger.critical("critical event")
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = (tmp_path / "application.log").read_text(encoding="utf-8")
    # DEBUG is below the default INFO level configured for the root logger
    # — deliberately absent, matching "do not log everything as INFO" /
    # DEBUG being development-only detail.
    assert "debug event" not in content
    assert "INFO" in content and "info event" in content
    assert "WARNING" in content and "warning event" in content
    assert "ERROR" in content and "error event" in content
    assert "CRITICAL" in content and "critical event" in content


def test_exception_logging_includes_a_traceback(tmp_path):
    configure_logging(log_dir=tmp_path)
    logger = get_logger("profiles.service")

    try:
        raise ValueError("simulated resume parsing failure")
    except ValueError:
        logger.exception("Resume parsing failed | %s", format_context(resume_id="abc123"))
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = (tmp_path / "application.log").read_text(encoding="utf-8")
    assert "Resume parsing failed" in content
    assert "Traceback (most recent call last)" in content
    assert "ValueError: simulated resume parsing failure" in content


class TestFormatContext:
    def test_renders_key_value_pairs(self):
        assert format_context(resume_id="abc123", user_id="xyz789") == "resume_id=abc123 user_id=xyz789"

    def test_skips_none_values(self):
        assert format_context(resume_id="abc123", profile_id=None) == "resume_id=abc123"

    def test_quotes_values_containing_whitespace(self):
        assert format_context(message="hello world") == 'message="hello world"'

    def test_empty_when_no_fields_given(self):
        assert format_context() == ""


def test_sensitive_field_names_are_never_hard_coded_into_any_instrumented_call_site():
    """Guards against a future regression: scans every `logger.<level>(...)`
    call site added by this change for the security-sensitive substrings
    the brief explicitly forbids logging (raw resume/base64 content, full
    outreach message bodies, credential-bearing connection strings). Only
    lines that are actually part of a logging call are checked — this
    module's other, entirely legitimate uses of e.g. `request.file_content`
    (the real decode-and-store code path) are not logging calls and must
    not trip this check.
    """
    import pathlib
    import re as re_module

    forbidden_in_log_calls = (
        "request.file_content",  # raw resume bytes/base64 — never log this
        "outreach.draft_message",  # full message body — log length/channel, not content
        "outreach.final_message",
        "password",
        "DATABASE_URL",  # may embed credentials — log the redacted target only
    )
    log_call_re = re_module.compile(r"logger\.\w+\(")

    src_root = pathlib.Path(__file__).resolve().parents[2] / "src"
    offending: list[str] = []
    for path in src_root.rglob("*.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        in_call = False
        for line in lines:
            if log_call_re.search(line):
                in_call = True
            if in_call:
                for needle in forbidden_in_log_calls:
                    if needle in line:
                        offending.append(f"{path}: {needle!r} in {line.strip()!r}")
                # A log call's closing paren is always the last non-trivial
                # character on some line within a few lines of the opener
                # for every call site added in this change (all are single
                # statements, not deeply nested) — good enough for this
                # regression guard without a full AST parse.
                if line.rstrip().endswith(")"):
                    in_call = False
    assert offending == []
