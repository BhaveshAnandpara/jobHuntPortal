"""Shared fixtures for the Outreach Generation LangGraph workflow's
node/graph tests. Reuses the fakes and factories from
`tests/outreach/conftest.py` (the same component's test tree) rather than
duplicating them — mirrors
`tests/workflows/langgraph/contact_discovery/conftest.py`'s pattern.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import outreach.events as events_module
import workflows.langgraph.outreach_generation.nodes as nodes_module
from outreach.db import set_session_factory

# Re-exported so tests under this directory can request the same
# SQLite-in-memory `session_factory`/`session` fixtures tests/outreach
# uses, without duplicating the fixture definition.
from tests.outreach.conftest import (  # noqa: F401
    FakeJobMatchClient,
    FakeLLMClient,
    FakeProfileServiceClient,
    make_job_match_response,
    make_ranked_contact,
    make_resume_profile,
    session,
    session_factory,
)

__all__: list[str] = []


@pytest.fixture(autouse=True)
def _reset_node_singletons() -> Iterator[None]:
    yield
    nodes_module.set_llm_client(None)
    events_module.set_event_producer(None)
    set_session_factory(None)
