"""Centralized application logging. See `config.py` for the full design
(format, rotation, service-name mapping) — this module only re-exports the
small public surface every other module needs.

Typical usage from any component::

    from infrastructure.logging import get_logger, format_context

    logger = get_logger(__name__)

    logger.info("Resume uploaded | %s", format_context(resume_id=resume.id, user_id=user_id))
"""

from __future__ import annotations

from infrastructure.logging.config import configure_logging, format_context, get_logger

__all__ = ["configure_logging", "format_context", "get_logger"]
