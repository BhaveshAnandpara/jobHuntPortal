"""Unit tests for the three ContactDiscoveryState node functions
(docs/architecture/langgraph-state.md#contactdiscoverystate).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

import workflows.langgraph.contact_discovery.nodes as nodes_module
from contacts.errors import ContactDiscoveryError
from contacts.repository import ContactRepository, ContactScoreRepository
from infrastructure.external.errors import PeopleSearchRequestError
from infrastructure.llm import LLMFailureReason, LLMProviderError
from shared.errors.codes import ErrorCode
from shared.types.dto import ContactCandidate
from shared.types.enums import ContactStatus, ContactType
from shared.types.ids import JobId, UserId
from tests.contacts.conftest import FakeLLMClient, FakePeopleSearchClient, make_hit
from workflows.langgraph.contact_discovery.discovery import (
    ContactClassificationBatch,
    ContactSearchPlan,
    HitClassification,
    RelevanceSignals,
)
from workflows.langgraph.contact_discovery.nodes import (
    persist_and_publish,
    rank_contacts,
    search_contacts,
)

pytestmark = pytest.mark.asyncio


def _initial_state(**overrides: object) -> dict:
    fields: dict[str, object] = {
        "job_id": JobId(uuid4()),
        "user_id": UserId(uuid4()),
        "company": "Acme Robotics",
        "title": "Senior Mechanical Design Engineer",
        "location": "Remote",
        "candidates": [],
        "ranked_contacts": [],
        "errors": [],
    }
    fields.update(overrides)
    return fields


def _candidate(**overrides: object) -> ContactCandidate:
    fields: dict[str, object] = {
        "full_name": "Jordan Smith",
        "headline": "Mechanical Design Engineer at Acme Robotics",
        "company": "Acme Robotics",
        "contact_type": ContactType.PRACTITIONER,
        "profile_url": "https://example.com/in/jordan-smith",
        "email": "jordan@example.com",
        "source": "static",
    }
    fields.update(overrides)
    return ContactCandidate(**fields)


# ---------------------------------------------------------------------------
# search_contacts
# ---------------------------------------------------------------------------


async def test_search_contacts_returns_classified_candidates() -> None:
    hits = [
        make_hit(full_name="Jordan Smith", headline="Mechanical Design Engineer"),
        make_hit(full_name="Riley Chen", headline="Engineering Manager"),
    ]
    nodes_module.set_people_search_client(FakePeopleSearchClient(hits))
    nodes_module.set_llm_client(
        FakeLLMClient(
            {
                "Target opportunity:": ContactClassificationBatch(
                    classifications=[
                        HitClassification(index=0, contact_type=ContactType.PRACTITIONER),
                        HitClassification(index=1, contact_type=ContactType.TEAM_LEAD),
                    ]
                ),
            },
            default=ContactSearchPlan(role_keywords=["Mechanical Design Engineer"]),
        )
    )

    result = await search_contacts(_initial_state())

    assert [c.full_name for c in result["candidates"]] == ["Jordan Smith", "Riley Chen"]
    assert result["candidates"][0].contact_type == ContactType.PRACTITIONER
    assert result["candidates"][1].contact_type == ContactType.TEAM_LEAD
    assert result["errors"] == []


async def test_search_contacts_no_hits_returns_empty_candidates() -> None:
    nodes_module.set_people_search_client(FakePeopleSearchClient([]))
    nodes_module.set_llm_client(
        FakeLLMClient(default=ContactSearchPlan(role_keywords=["Engineer"]))
    )

    result = await search_contacts(_initial_state())

    assert result["candidates"] == []
    assert result["errors"] == []


async def test_search_contacts_people_search_failure_records_error_and_routes_forward() -> None:
    """Scenario E: external search failure -> normalized error recorded,
    candidates left empty, workflow still reaches a valid state (not a raw
    unhandled exception)."""
    nodes_module.set_people_search_client(
        FakePeopleSearchClient(error=PeopleSearchRequestError("provider unavailable"))
    )
    nodes_module.set_llm_client(
        FakeLLMClient(default=ContactSearchPlan(role_keywords=["Engineer"]))
    )

    result = await search_contacts(_initial_state())

    assert result["candidates"] == []
    assert len(result["errors"]) == 1
    assert result["errors"][0].error_code == ErrorCode.CONTACT_SEARCH_FAILED
    assert result["errors"][0].node == "search_contacts"


async def test_search_contacts_llm_query_construction_failure_falls_back_to_title() -> None:
    hits = [make_hit(full_name="Jordan Smith")]
    fake_search = FakePeopleSearchClient(hits)
    nodes_module.set_people_search_client(fake_search)
    error = LLMProviderError(
        LLMFailureReason.TIMEOUT, "timed out", provider="fake", model="fake-model"
    )
    nodes_module.set_llm_client(
        FakeLLMClient(
            {
                "Target opportunity:": ContactClassificationBatch(
                    classifications=[
                        HitClassification(index=0, contact_type=ContactType.PRACTITIONER)
                    ]
                ),
            },
            default=error,
        )
    )

    result = await search_contacts(_initial_state(title="Mechanical Design Engineer"))

    # Falls back to using the job title itself as the sole search keyword
    # rather than aborting the node.
    assert fake_search.queries[0].role_keywords == ["Mechanical Design Engineer"]
    assert len(result["candidates"]) == 1


async def test_search_contacts_classification_failure_falls_back_to_other() -> None:
    hits = [make_hit(full_name="Jordan Smith")]
    nodes_module.set_people_search_client(FakePeopleSearchClient(hits))
    classification_error = LLMProviderError(
        LLMFailureReason.CONNECTION_ERROR, "unreachable", provider="fake", model="fake-model"
    )
    nodes_module.set_llm_client(
        FakeLLMClient(
            {"Target opportunity:": classification_error},
            default=ContactSearchPlan(role_keywords=["Engineer"]),
        )
    )

    result = await search_contacts(_initial_state())

    assert len(result["candidates"]) == 1
    assert result["candidates"][0].contact_type == ContactType.OTHER
    assert len(result["errors"]) == 1
    assert result["errors"][0].error_code == ErrorCode.LLM_PROVIDER_ERROR


# ---------------------------------------------------------------------------
# rank_contacts
# ---------------------------------------------------------------------------


async def test_rank_contacts_orders_by_relevance_score_descending() -> None:
    strong = _candidate(full_name="Strong Fit", headline="Mechanical Design Engineer")
    weak = _candidate(full_name="Weak Fit", headline="Marketing Coordinator")
    nodes_module.set_llm_client(
        FakeLLMClient(
            {
                "Contact: Strong Fit": RelevanceSignals(
                    role_similarity=0.95, department_relevance=0.9, seniority_fit=0.8
                ),
                "Contact: Weak Fit": RelevanceSignals(
                    role_similarity=0.05, department_relevance=0.1, seniority_fit=0.2
                ),
            }
        )
    )

    state = _initial_state(candidates=[weak, strong])  # deliberately out of order
    result = await rank_contacts(state)

    ranked = result["ranked_contacts"]
    assert [r.full_name for r in ranked] == ["Strong Fit", "Weak Fit"]
    assert ranked[0].relevance_score > ranked[1].relevance_score
    assert result["errors"] == []


async def test_rank_contacts_empty_candidates_returns_empty_ranked_contacts() -> None:
    result = await rank_contacts(_initial_state(candidates=[]))
    assert result["ranked_contacts"] == []


async def test_rank_contacts_llm_failure_falls_back_to_rule_based_signals() -> None:
    """Scenario D: missing optional profile/activity fields
    (department_relevance, seniority_fit both None) -> ranking still
    succeeds, no crash. LLM_PROVIDER_ERROR falls back to rule-based
    same_company/role_similarity only."""
    candidate = _candidate(
        full_name="Jordan Smith",
        headline="Mechanical Design Engineer",
        company="Acme Robotics",
    )
    error = LLMProviderError(
        LLMFailureReason.PROVIDER_ERROR, "provider down", provider="fake", model="fake-model"
    )
    nodes_module.set_llm_client(FakeLLMClient(default=error))

    state = _initial_state(
        company="Acme Robotics",
        title="Senior Mechanical Design Engineer",
        candidates=[candidate],
    )
    result = await rank_contacts(state)

    assert len(result["ranked_contacts"]) == 1
    ranked = result["ranked_contacts"][0]
    assert 0.0 <= ranked.relevance_score <= 10.0
    assert len(result["errors"]) == 1
    assert result["errors"][0].error_code == ErrorCode.LLM_PROVIDER_ERROR
    assert result["errors"][0].node == "rank_contacts"

    # The rule-based fallback path's persisted detail carries None for the
    # two LLM-only signals — verified via a full run through
    # persist_and_publish in test_persist_and_publish_handles_missing_optional_signals.


# ---------------------------------------------------------------------------
# persist_and_publish
# ---------------------------------------------------------------------------


async def _run_rank_then_persist(state: dict) -> dict:
    """Drives rank_contacts -> persist_and_publish through the same
    context-var side channel `contacts.consumers._run_discovery` sets up
    around a real graph invocation (workflows/langgraph/contact_discovery
    /context.py) — required because `rank_contacts` and `persist_and_publish`
    communicate contact persistence detail via that side channel, not via
    `ContactDiscoveryState` itself (see context.py's docstring).
    """
    from workflows.langgraph.contact_discovery.context import (
        reset_contact_details,
        set_contact_details,
    )

    token = set_contact_details({})
    try:
        ranked_state = await rank_contacts(state)
        return await persist_and_publish(ranked_state)
    finally:
        reset_contact_details(token)


def _wire_persistence(session_factory, broker) -> None:
    from contacts.db import set_session_factory
    from contacts.events import set_event_producer
    from infrastructure.kafka.in_memory import InMemoryProducerClient
    from infrastructure.kafka.producer import EventProducer

    set_session_factory(session_factory)
    set_event_producer(
        EventProducer("contact-discovery-service", client=InMemoryProducerClient(broker))
    )


async def test_persist_and_publish_persists_contacts_and_scores_at_ranked_status(
    session_factory,
) -> None:
    """Scenario H: contacts/contact_rankings rows stored under correct
    ownership, Contact.status set to RANKED."""
    from infrastructure.kafka.in_memory import InMemoryBroker
    from infrastructure.kafka.serialization import deserialize
    from infrastructure.kafka.topics import Topic

    broker = InMemoryBroker()
    _wire_persistence(session_factory, broker)
    nodes_module.set_llm_client(
        FakeLLMClient(
            {
                "Contact: Jordan Smith": RelevanceSignals(
                    role_similarity=0.9, department_relevance=0.8, seniority_fit=0.7
                ),
            }
        )
    )

    candidate = _candidate(
        full_name="Jordan Smith", email="jordan@example.com", company="Acme Robotics"
    )
    job_id = JobId(uuid4())
    user_id = UserId(uuid4())
    state = _initial_state(
        job_id=job_id, user_id=user_id, company="Acme Robotics", candidates=[candidate]
    )

    await _run_rank_then_persist(state)

    async with session_factory() as session:
        contacts = await ContactRepository(session).list_for_job(job_id)
        assert len(contacts) == 1
        stored_contact = contacts[0]
        assert stored_contact.full_name == "Jordan Smith"
        assert stored_contact.email == "jordan@example.com"
        assert stored_contact.user_id == user_id
        assert stored_contact.status == ContactStatus.RANKED

        score = await ContactScoreRepository(session).get_for_contact(stored_contact.id)
        assert score is not None
        assert score.same_company is True
        assert score.role_similarity == pytest.approx(0.9)
        assert score.department_relevance == pytest.approx(0.8)
        assert score.seniority_fit == pytest.approx(0.7)
        assert 0.0 <= score.relevance_score <= 10.0

    messages = broker.log(Topic.CONTACTS_FOUND.value)
    assert len(messages) == 1
    published = deserialize(Topic.CONTACTS_FOUND, messages[0].value())
    assert published.payload.job_id == job_id
    assert len(published.payload.contacts) == 1


async def test_persist_and_publish_handles_missing_optional_signals(session_factory) -> None:
    """Scenario D (full slice): a rule-based-fallback ranked contact (no
    department_relevance/seniority_fit) persists without crashing, with
    those columns stored as NULL."""
    from infrastructure.kafka.in_memory import InMemoryBroker

    broker = InMemoryBroker()
    _wire_persistence(session_factory, broker)
    error = LLMProviderError(
        LLMFailureReason.PROVIDER_ERROR, "provider down", provider="fake", model="fake-model"
    )
    nodes_module.set_llm_client(FakeLLMClient(default=error))

    candidate = _candidate(full_name="Jordan Smith", company="Acme Robotics")
    job_id = JobId(uuid4())
    state = _initial_state(job_id=job_id, company="Acme Robotics", candidates=[candidate])

    await _run_rank_then_persist(state)

    async with session_factory() as session:
        contacts = await ContactRepository(session).list_for_job(job_id)
        assert len(contacts) == 1
        score = await ContactScoreRepository(session).get_for_contact(contacts[0].id)
        assert score is not None
        assert score.department_relevance is None
        assert score.seniority_fit is None
        assert score.role_similarity is not None  # rule-based fallback still populates this


async def test_persist_and_publish_no_contacts_publishes_empty_event(session_factory) -> None:
    """Scenario F: no contacts found -> valid empty ContactsFoundEvent
    published, not silently dropped."""
    from infrastructure.kafka.in_memory import InMemoryBroker
    from infrastructure.kafka.serialization import deserialize
    from infrastructure.kafka.topics import Topic

    broker = InMemoryBroker()
    _wire_persistence(session_factory, broker)

    job_id = JobId(uuid4())
    state = _initial_state(job_id=job_id, candidates=[], ranked_contacts=[])

    await persist_and_publish(state)

    async with session_factory() as session:
        contacts = await ContactRepository(session).list_for_job(job_id)
        assert contacts == []

    messages = broker.log(Topic.CONTACTS_FOUND.value)
    assert len(messages) == 1
    published = deserialize(Topic.CONTACTS_FOUND, messages[0].value())
    assert published.payload.job_id == job_id
    assert published.payload.contacts == []


async def test_persist_and_publish_raises_on_db_failure(session_factory, monkeypatch) -> None:
    from infrastructure.kafka.in_memory import InMemoryBroker

    broker = InMemoryBroker()
    _wire_persistence(session_factory, broker)

    async def _broken_add(self, contact):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(ContactRepository, "add", _broken_add)

    from shared.types.dto import RankedContact
    from workflows.langgraph.contact_discovery.context import (
        ContactPersistenceDetail,
        reset_contact_details,
        set_contact_details,
    )

    contact_id = uuid4()
    ranked_contact = RankedContact(
        contact_id=contact_id,
        full_name="Jordan Smith",
        headline="Mechanical Design Engineer",
        contact_type=ContactType.PRACTITIONER,
        profile_url=None,
        relevance_score=7.5,
    )
    token = set_contact_details(
        {
            contact_id: ContactPersistenceDetail(
                company="Acme Robotics",
                email=None,
                same_company=True,
                role_similarity=0.8,
                department_relevance=0.7,
                seniority_fit=0.6,
            )
        }
    )
    try:
        state = _initial_state(ranked_contacts=[ranked_contact])
        with pytest.raises(ContactDiscoveryError):
            await persist_and_publish(state)
    finally:
        reset_contact_details(token)


async def test_persist_and_publish_raises_on_publish_failure(session_factory) -> None:
    from contacts.db import set_session_factory
    from contacts.events import set_event_producer

    set_session_factory(session_factory)

    class BrokenProducer:
        def publish(self, *args, **kwargs):
            raise RuntimeError("kafka broker unreachable")

    set_event_producer(BrokenProducer())

    state = _initial_state(ranked_contacts=[])

    with pytest.raises(ContactDiscoveryError):
        await persist_and_publish(state)
