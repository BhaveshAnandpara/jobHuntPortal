"""Shared fixtures for the Contact Discovery LangGraph workflow's node/graph
tests. Reuses the fakes and factories from `tests/contacts/conftest.py` (the
same component's test tree) rather than duplicating them — mirrors
`tests/workflows/langgraph/job_matching/conftest.py`'s pattern.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import contacts.events as events_module
import workflows.langgraph.contact_discovery.nodes as nodes_module
from contacts.db import set_session_factory

# Re-exported so tests under this directory can request the same
# SQLite-in-memory `session_factory`/`session` fixtures tests/contacts uses,
# without duplicating the fixture definition.
from tests.contacts.conftest import (  # noqa: F401
    FakeLLMClient,
    FakePeopleSearchClient,
    make_hit,
    make_search_request,
    session,
    session_factory,
)

__all__: list[str] = []


@pytest.fixture(autouse=True)
def _reset_node_singletons() -> Iterator[None]:
    yield
    nodes_module.set_people_search_client(None)
    nodes_module.set_llm_client(None)
    events_module.set_event_producer(None)
    set_session_factory(None)
