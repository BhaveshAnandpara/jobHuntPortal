"""Full-graph integration tests for the ContactDiscoveryState workflow
(docs/architecture/langgraph-state.md#contactdiscoverystate), against the
compiled graph from `workflows.langgraph.contact_discovery.graph.build_graph`.

Covers task scenarios A (software engineering), B (mechanical engineering —
same code path as A, proving no hard-coded profession branch), C (HR), E
(external search failure), and F (no contacts found).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

import contacts.events as events_module
import workflows.langgraph.contact_discovery.nodes as nodes_module
from contacts.db import set_session_factory
from contacts.repository import ContactRepository
from infrastructure.external.errors import PeopleSearchRequestError
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize
from infrastructure.kafka.topics import Topic
from shared.errors.codes import ErrorCode
from shared.types.enums import ContactType
from shared.types.ids import JobId, UserId
from tests.contacts.conftest import FakeLLMClient, FakePeopleSearchClient, make_hit
from workflows.langgraph.contact_discovery.context import (
    reset_contact_details,
    reset_correlation_id,
    set_contact_details,
    set_correlation_id,
)
from workflows.langgraph.contact_discovery.discovery import (
    ContactClassificationBatch,
    ContactSearchPlan,
    HitClassification,
    RankedRelevanceSignals,
    RelevanceSignalsBatch,
)
from workflows.langgraph.contact_discovery.graph import build_graph

pytestmark = pytest.mark.asyncio


def _initial_state(job_id, user_id, *, company, title, location="Remote") -> dict:
    return {
        "job_id": job_id,
        "user_id": user_id,
        "company": company,
        "title": title,
        "location": location,
        "candidates": [],
        "ranked_contacts": [],
        "errors": [],
    }


async def _run_graph(state: dict) -> dict:
    """Drives the compiled graph the same way `contacts.consumers
    ._run_discovery` does — via the context-var side channel, since
    `persist_and_publish` depends on it (workflows/langgraph/
    contact_discovery/context.py)."""
    correlation_token = set_correlation_id(None)
    details_token = set_contact_details({})
    try:
        return await build_graph().ainvoke(state)
    finally:
        reset_contact_details(details_token)
        reset_correlation_id(correlation_token)


def _wire(session_factory, *, hits, classifications, ranking_signals) -> InMemoryBroker:
    broker = InMemoryBroker()
    set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("contact-discovery-service", client=InMemoryProducerClient(broker))
    )
    nodes_module.set_people_search_client(FakePeopleSearchClient(hits))
    llm_results = {"Target opportunity:": classifications, **ranking_signals}
    nodes_module.set_llm_client(
        FakeLLMClient(llm_results, default=ContactSearchPlan(role_keywords=["Engineer"]))
    )
    return broker


# ---------------------------------------------------------------------------
# Scenario A — software engineering opportunity
# ---------------------------------------------------------------------------


async def test_software_engineering_opportunity_ranks_technical_contacts(
    session_factory,
) -> None:
    job_id, user_id = JobId(uuid4()), UserId(uuid4())
    hits = [
        make_hit(full_name="Priya Patel", headline="Staff Software Engineer", company="Acme Software"),
        make_hit(full_name="Sam Lee", headline="Engineering Manager", company="Acme Software"),
        make_hit(full_name="Alex Kim", headline="Technical Recruiter", company="Acme Software"),
    ]
    classifications = ContactClassificationBatch(
        classifications=[
            HitClassification(index=0, contact_type=ContactType.PRACTITIONER),
            HitClassification(index=1, contact_type=ContactType.HIRING_MANAGER),
            HitClassification(index=2, contact_type=ContactType.RECRUITER),
        ]
    )
    ranking_signals = {
        "Score EVERY contact": RelevanceSignalsBatch(
            signals=[
                RankedRelevanceSignals(
                    index=0, role_similarity=0.95, department_relevance=0.9, seniority_fit=0.8
                ),
                RankedRelevanceSignals(
                    index=1, role_similarity=0.7, department_relevance=0.85, seniority_fit=0.75
                ),
                RankedRelevanceSignals(
                    index=2, role_similarity=0.3, department_relevance=0.4, seniority_fit=0.5
                ),
            ]
        ),
    }
    broker = _wire(session_factory, hits=hits, classifications=classifications, ranking_signals=ranking_signals)

    final_state = await _run_graph(
        _initial_state(job_id, user_id, company="Acme Software", title="Backend Software Engineer")
    )

    ranked = final_state["ranked_contacts"]
    assert {r.contact_type for r in ranked} == {
        ContactType.PRACTITIONER,
        ContactType.HIRING_MANAGER,
        ContactType.RECRUITER,
    }
    assert ranked[0].full_name == "Priya Patel"  # highest role_similarity, wins ranking
    assert len(broker.log(Topic.CONTACTS_FOUND.value)) == 1

    async with session_factory() as session:
        stored = await ContactRepository(session).list_for_job(job_id)
        assert len(stored) == 3


# ---------------------------------------------------------------------------
# Scenario B — mechanical engineering opportunity (same code path as A)
# ---------------------------------------------------------------------------


async def test_mechanical_engineering_opportunity_ranks_design_contacts(session_factory) -> None:
    job_id, user_id = JobId(uuid4()), UserId(uuid4())
    hits = [
        make_hit(full_name="Jordan Smith", headline="Mechanical Design Engineer"),
        make_hit(full_name="Riley Chen", headline="Design Lead"),
        make_hit(full_name="Morgan Blake", headline="Manufacturing Recruiter"),
    ]
    classifications = ContactClassificationBatch(
        classifications=[
            HitClassification(index=0, contact_type=ContactType.PRACTITIONER),
            HitClassification(index=1, contact_type=ContactType.TEAM_LEAD),
            HitClassification(index=2, contact_type=ContactType.RECRUITER),
        ]
    )
    ranking_signals = {
        "Score EVERY contact": RelevanceSignalsBatch(
            signals=[
                RankedRelevanceSignals(
                    index=0, role_similarity=0.92, department_relevance=0.88, seniority_fit=0.7
                ),
                RankedRelevanceSignals(
                    index=1, role_similarity=0.8, department_relevance=0.85, seniority_fit=0.8
                ),
                RankedRelevanceSignals(
                    index=2, role_similarity=0.25, department_relevance=0.3, seniority_fit=0.4
                ),
            ]
        ),
    }
    broker = _wire(session_factory, hits=hits, classifications=classifications, ranking_signals=ranking_signals)

    final_state = await _run_graph(
        _initial_state(
            job_id, user_id, company="Acme Robotics", title="Senior Mechanical Design Engineer"
        )
    )

    ranked = final_state["ranked_contacts"]
    assert {r.contact_type for r in ranked} == {
        ContactType.PRACTITIONER,
        ContactType.TEAM_LEAD,
        ContactType.RECRUITER,
    }
    assert ranked[0].full_name == "Jordan Smith"
    assert len(broker.log(Topic.CONTACTS_FOUND.value)) == 1


# ---------------------------------------------------------------------------
# Scenario C — HR opportunity (same code path again)
# ---------------------------------------------------------------------------


async def test_hr_opportunity_ranks_recruiting_contacts(session_factory) -> None:
    job_id, user_id = JobId(uuid4()), UserId(uuid4())
    hits = [
        make_hit(full_name="Casey Nguyen", headline="Talent Acquisition Specialist", company="Acme Corp"),
        make_hit(full_name="Drew Patel", headline="HR Business Partner", company="Acme Corp"),
        make_hit(full_name="Taylor Reed", headline="VP of People", company="Acme Corp"),
    ]
    classifications = ContactClassificationBatch(
        classifications=[
            HitClassification(index=0, contact_type=ContactType.RECRUITER),
            HitClassification(index=1, contact_type=ContactType.PRACTITIONER),
            HitClassification(index=2, contact_type=ContactType.EXECUTIVE),
        ]
    )
    ranking_signals = {
        "Score EVERY contact": RelevanceSignalsBatch(
            signals=[
                RankedRelevanceSignals(
                    index=0, role_similarity=0.9, department_relevance=0.9, seniority_fit=0.75
                ),
                RankedRelevanceSignals(
                    index=1, role_similarity=0.85, department_relevance=0.9, seniority_fit=0.7
                ),
                RankedRelevanceSignals(
                    index=2, role_similarity=0.4, department_relevance=0.6, seniority_fit=0.5
                ),
            ]
        ),
    }
    broker = _wire(session_factory, hits=hits, classifications=classifications, ranking_signals=ranking_signals)

    final_state = await _run_graph(
        _initial_state(job_id, user_id, company="Acme Corp", title="HR Business Partner")
    )

    ranked = final_state["ranked_contacts"]
    assert {r.contact_type for r in ranked} == {
        ContactType.RECRUITER,
        ContactType.PRACTITIONER,
        ContactType.EXECUTIVE,
    }
    assert ranked[0].full_name == "Casey Nguyen"
    assert len(broker.log(Topic.CONTACTS_FOUND.value)) == 1


# ---------------------------------------------------------------------------
# Scenario E — external search failure: valid terminal state, not a crash
# ---------------------------------------------------------------------------


async def test_search_failure_still_reaches_valid_terminal_state_with_empty_event(
    session_factory,
) -> None:
    job_id, user_id = JobId(uuid4()), UserId(uuid4())
    set_session_factory(session_factory)
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("contact-discovery-service", client=InMemoryProducerClient(broker))
    )
    nodes_module.set_people_search_client(
        FakePeopleSearchClient(error=PeopleSearchRequestError("provider unavailable"))
    )
    nodes_module.set_llm_client(
        FakeLLMClient(default=ContactSearchPlan(role_keywords=["Engineer"]))
    )

    final_state = await _run_graph(
        _initial_state(job_id, user_id, company="Acme Robotics", title="Mechanical Engineer")
    )

    assert final_state["candidates"] == []
    assert final_state["ranked_contacts"] == []
    assert any(e.error_code == ErrorCode.CONTACT_SEARCH_FAILED for e in final_state["errors"])

    messages = broker.log(Topic.CONTACTS_FOUND.value)
    assert len(messages) == 1
    published = deserialize(Topic.CONTACTS_FOUND, messages[0].value())
    assert published.payload.contacts == []


# ---------------------------------------------------------------------------
# Scenario F — no contacts found: valid empty ContactsFoundEvent published
# ---------------------------------------------------------------------------


async def test_no_hits_publishes_valid_empty_contacts_found_event(session_factory) -> None:
    job_id, user_id = JobId(uuid4()), UserId(uuid4())
    set_session_factory(session_factory)
    broker = InMemoryBroker()
    events_module.set_event_producer(
        EventProducer("contact-discovery-service", client=InMemoryProducerClient(broker))
    )
    nodes_module.set_people_search_client(FakePeopleSearchClient([]))
    nodes_module.set_llm_client(
        FakeLLMClient(default=ContactSearchPlan(role_keywords=["Engineer"]))
    )

    final_state = await _run_graph(
        _initial_state(job_id, user_id, company="Acme Robotics", title="Mechanical Engineer")
    )

    assert final_state["ranked_contacts"] == []

    messages = broker.log(Topic.CONTACTS_FOUND.value)
    assert len(messages) == 1
    published = deserialize(Topic.CONTACTS_FOUND, messages[0].value())
    assert published.payload.job_id == job_id
    assert published.payload.contacts == []

    async with session_factory() as session:
        stored = await ContactRepository(session).list_for_job(job_id)
        assert stored == []
