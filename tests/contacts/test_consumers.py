"""Tests for `contacts.consumers` — the `contacts.requested` Kafka
consumer.

Scenario G (Kafka): a `contacts.requested` envelope consumed,
`correlation_id` preserved end-to-end into the published `contacts.found`
envelope, with the right payload shape. Also covers this component's
idempotency mechanism (see `contacts.consumers._handle_contacts_requested_async`'s
docstring for the chosen "does a contacts row already exist" check).

Most tests exercise `_handle_contacts_requested_async` directly (awaited
in-loop) rather than the outer sync `handle_contacts_requested` — mirrors
`tests/matching/test_consumers.py`'s established rationale.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

import contacts.consumers as consumers_module
import contacts.db as db_module
import contacts.events as events_module
import workflows.langgraph.contact_discovery.nodes as nodes_module
from contacts.errors import ContactDiscoveryError
from contacts.repository import ContactRepository
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import build_envelope, deserialize
from infrastructure.kafka.topics import Topic
from shared.types.enums import ContactType
from shared.types.ids import CorrelationId
from tests.contacts.conftest import (
    FakeLLMClient,
    FakePeopleSearchClient,
    make_hit,
    make_search_request,
)
from workflows.langgraph.contact_discovery.discovery import (
    ContactClassificationBatch,
    ContactSearchPlan,
    HitClassification,
    RankedRelevanceSignals,
    RelevanceSignalsBatch,
)


def _wire(
    session_factory,
    *,
    broker: InMemoryBroker,
    hits=None,
    classifications=None,
) -> None:
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("contact-discovery-service", client=InMemoryProducerClient(broker))
    )
    nodes_module.set_people_search_client(FakePeopleSearchClient(hits or []))
    llm_results = {}
    if classifications is not None:
        llm_results["Target opportunity:"] = classifications
    if hits:
        llm_results["Score EVERY contact"] = RelevanceSignalsBatch(
            signals=[
                RankedRelevanceSignals(
                    index=i, role_similarity=0.8, department_relevance=0.7, seniority_fit=0.6
                )
                for i in range(len(hits))
            ]
        )
    nodes_module.set_llm_client(
        FakeLLMClient(llm_results, default=ContactSearchPlan(role_keywords=["Engineer"]))
    )


@pytest.mark.asyncio
async def test_handle_contacts_requested_persists_and_publishes_with_correlation_id(
    session_factory,
) -> None:
    request = make_search_request()
    hits = [make_hit(full_name="Jordan Smith", headline="Mechanical Design Engineer")]
    classifications = ContactClassificationBatch(
        classifications=[HitClassification(index=0, contact_type=ContactType.PRACTITIONER)]
    )
    broker = InMemoryBroker()
    _wire(session_factory, broker=broker, hits=hits, classifications=classifications)

    envelope = build_envelope(
        Topic.CONTACTS_REQUESTED,
        request,
        producer="job-matching-service",
        correlation_id=CorrelationId(uuid4()),
    )

    await consumers_module._handle_contacts_requested_async(envelope)

    messages = broker.log(Topic.CONTACTS_FOUND.value)
    assert len(messages) == 1

    published = deserialize(Topic.CONTACTS_FOUND, messages[0].value())
    assert published.correlation_id == envelope.correlation_id
    assert published.payload.job_id == request.job_id
    assert published.payload.user_id == request.user_id
    assert len(published.payload.contacts) == 1
    assert published.payload.contacts[0].full_name == "Jordan Smith"

    async with session_factory() as session:
        stored = await ContactRepository(session).list_for_job(request.job_id)
        assert len(stored) == 1


@pytest.mark.asyncio
async def test_handle_contacts_requested_no_hits_publishes_empty_event(session_factory) -> None:
    request = make_search_request()
    broker = InMemoryBroker()
    _wire(session_factory, broker=broker, hits=[])

    envelope = build_envelope(Topic.CONTACTS_REQUESTED, request, producer="job-matching-service")

    await consumers_module._handle_contacts_requested_async(envelope)

    messages = broker.log(Topic.CONTACTS_FOUND.value)
    assert len(messages) == 1
    published = deserialize(Topic.CONTACTS_FOUND, messages[0].value())
    assert published.payload.contacts == []


@pytest.mark.asyncio
async def test_handle_contacts_requested_skips_replayed_message_once_persisted(
    session_factory,
) -> None:
    """Idempotency: a redelivery that finds contacts rows already persisted
    for this job_id skips re-running the workflow entirely (no duplicate
    people-search/LLM calls, no duplicate publish) — this component's
    documented, deliberately simpler alternative to Job Matching's
    published_at marker (see consumers.py's docstring for the full
    reasoning)."""
    request = make_search_request()
    hits = [make_hit(full_name="Jordan Smith", headline="Mechanical Design Engineer")]
    classifications = ContactClassificationBatch(
        classifications=[HitClassification(index=0, contact_type=ContactType.PRACTITIONER)]
    )
    broker = InMemoryBroker()
    _wire(session_factory, broker=broker, hits=hits, classifications=classifications)

    envelope = build_envelope(Topic.CONTACTS_REQUESTED, request, producer="job-matching-service")

    await consumers_module._handle_contacts_requested_async(envelope)
    assert len(broker.log(Topic.CONTACTS_FOUND.value)) == 1

    people_search_client = nodes_module._people_search_client
    llm_client = nodes_module._llm_client

    # Redeliver the identical message.
    await consumers_module._handle_contacts_requested_async(envelope)

    assert len(broker.log(Topic.CONTACTS_FOUND.value)) == 1  # no duplicate publish
    assert len(people_search_client.queries) == 1  # not called a second time
    # search-plan + classification + one ranking call (one candidate) —
    # not called again on the redelivery.
    assert len(llm_client.prompts) == 3


@pytest.mark.asyncio
async def test_handle_contacts_requested_logs_the_underlying_search_failure(
    session_factory, caplog
) -> None:
    """A people-search provider failure still routes forward to an empty
    `contacts.found` (per `search_contacts`'s documented contract), but the
    "no contacts found" WARNING must carry the real failure reason — not
    just "zero results" indistinguishable from a legitimate empty search.
    """
    from infrastructure.external.errors import PeopleSearchRequestError

    request = make_search_request()
    broker = InMemoryBroker()
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("contact-discovery-service", client=InMemoryProducerClient(broker))
    )
    nodes_module.set_people_search_client(
        FakePeopleSearchClient(
            error=PeopleSearchRequestError(
                "people search failed: 403 Client Error", provider="public-web-search"
            )
        )
    )
    nodes_module.set_llm_client(
        FakeLLMClient(default=ContactSearchPlan(role_keywords=["Engineer"]))
    )

    envelope = build_envelope(Topic.CONTACTS_REQUESTED, request, producer="job-matching-service")

    with caplog.at_level("WARNING"):
        await consumers_module._handle_contacts_requested_async(envelope)

    warning_lines = [
        r.message for r in caplog.records if "no contacts found" in r.message
    ]
    assert warning_lines
    assert "403 Client Error" in warning_lines[0]
    assert "search_contacts" in warning_lines[0]


@pytest.mark.asyncio
async def test_handle_contacts_requested_wraps_llm_provider_error(session_factory) -> None:
    from infrastructure.llm import LLMFailureReason, LLMProviderError

    request = make_search_request()
    broker = InMemoryBroker()
    db_module.set_session_factory(session_factory)
    events_module.set_event_producer(
        EventProducer("contact-discovery-service", client=InMemoryProducerClient(broker))
    )
    nodes_module.set_people_search_client(
        FakePeopleSearchClient([make_hit(full_name="Jordan Smith")])
    )
    # classify_hits raises unconditionally; search plan also has no
    # scripted result, so build_search_plan's own LLMProviderError is
    # swallowed by nodes.search_contacts' documented fallback — the
    # classification step's failure is what propagates all the way to
    # persist_and_publish's contacts.found (LLM_PROVIDER_ERROR there is
    # non-fatal per the node contract), so to actually exercise the
    # consumer's LLM_PROVIDER_ERROR wrapping we fail persistence instead
    # via a broken repository add, keeping this test focused on the
    # consumer's exception-normalization behavior.
    nodes_module.set_llm_client(
        FakeLLMClient(default=ContactSearchPlan(role_keywords=["Engineer"]))
    )

    envelope = build_envelope(Topic.CONTACTS_REQUESTED, request, producer="job-matching-service")

    # search_contacts' own LLM classification failure is handled
    # internally (falls back to OTHER) and does not raise — so exercise the
    # *consumer's* LLMProviderError normalization path directly instead by
    # monkeypatching the graph to raise one.
    async def _broken_graph_ainvoke(state):
        raise LLMProviderError(
            LLMFailureReason.TIMEOUT, "timed out", provider="fake", model="fake-model"
        )

    class _BrokenGraph:
        ainvoke = staticmethod(_broken_graph_ainvoke)

    consumers_module.set_graph(_BrokenGraph())

    with pytest.raises(ContactDiscoveryError) as exc_info:
        await consumers_module._handle_contacts_requested_async(envelope)

    from shared.errors.codes import ErrorCode

    assert exc_info.value.error_code == ErrorCode.LLM_PROVIDER_ERROR


def test_handle_contacts_requested_sync_wrapper_drives_the_async_body(monkeypatch) -> None:
    """The outer `handle_contacts_requested` must be a plain sync function
    (matching `EventConsumer`'s `EventHandler` contract) that actually
    executes `_handle_contacts_requested_async` via `asyncio.run`."""
    calls: list[object] = []

    async def fake_async_handler(envelope: object) -> None:
        calls.append(envelope)

    monkeypatch.setattr(
        consumers_module, "_handle_contacts_requested_async", fake_async_handler
    )

    request = make_search_request()
    envelope = build_envelope(Topic.CONTACTS_REQUESTED, request, producer="job-matching-service")

    consumers_module.handle_contacts_requested(envelope)

    assert calls == [envelope]


def test_handle_contacts_requested_sync_wrapper_enforces_a_hard_timeout(monkeypatch) -> None:
    """A handler that never returns must not block this thread forever —
    `asyncio.wait_for`'s `DEFAULT_HANDLER_TIMEOUT_SECONDS` ceiling (see that
    constant's docstring) must fire and surface as a plain `TimeoutError`
    so `EventConsumer`'s existing retry/DLQ path can take over. Patches the
    ceiling to a tiny value so this test does not actually wait minutes.
    """
    import infrastructure.kafka.consumer as consumer_module

    monkeypatch.setattr(consumer_module, "DEFAULT_HANDLER_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(consumers_module, "DEFAULT_HANDLER_TIMEOUT_SECONDS", 0.05)

    async def hanging_handler(envelope: object) -> None:
        import asyncio

        await asyncio.sleep(999)

    monkeypatch.setattr(consumers_module, "_handle_contacts_requested_async", hanging_handler)

    request = make_search_request()
    envelope = build_envelope(Topic.CONTACTS_REQUESTED, request, producer="job-matching-service")

    with pytest.raises(TimeoutError):
        consumers_module.handle_contacts_requested(envelope)
