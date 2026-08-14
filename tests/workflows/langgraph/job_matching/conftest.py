"""Shared fixtures for the Job Matching LangGraph workflow's node/graph
tests. Reuses the fakes and domain factories from `tests/matching/conftest.py`
(the same component's test tree) rather than duplicating them.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import matching.events as events_module
import workflows.langgraph.job_matching.nodes as nodes_module
from matching.db import set_session_factory

# Re-exported so tests under this directory can request the same
# SQLite-in-memory `session_factory`/`session` fixtures tests/matching uses,
# without duplicating the fixture definition.
from tests.matching.conftest import session, session_factory  # noqa: F401

__all__: list[str] = []


@pytest.fixture(autouse=True)
def _reset_node_singletons() -> Iterator[None]:
    yield
    nodes_module.set_profile_service_client(None)
    nodes_module.set_llm_client(None)
    events_module.set_event_producer(None)
    set_session_factory(None)
