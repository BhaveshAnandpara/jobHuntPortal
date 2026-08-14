"""Full-graph integration tests for the OutreachGenerationState workflow
(docs/architecture/langgraph-state.md#outreachgenerationstate), against the
compiled graph from
`workflows.langgraph.outreach_generation.graph.build_graph`.

Covers task scenarios A (email), B (LinkedIn connection request), C
(cross-profession: software engineer / mechanical engineer / HR — same
graph, same node functions), and confirms the terminal state is always
`PENDING_APPROVAL` (never `SENT`) — the first half of Scenario D's
Generated -> Attempted Send -> Blocked trace (the second half, that the
send worker itself refuses to act on a non-APPROVED row, is proven in
`tests/outreach/test_consumers.py`).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

import workflows.langgraph.outreach_generation.nodes as nodes_module
from infrastructure.kafka.in_memory import InMemoryBroker, InMemoryProducerClient
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.serialization import deserialize
from infrastructure.kafka.topics import Topic
from outreach.db import set_session_factory
from outreach.events import set_event_producer
from outreach.repository import OutreachRepository
from shared.types.enums import OutreachChannel, OutreachStatus
from shared.types.ids import JobId, ResumeId, UserId
from tests.outreach.conftest import (
    FakeLLMClient,
    make_ranked_contact,
    make_resume_profile,
)
from workflows.langgraph.outreach_generation.context import (
    JobContext,
    reset_correlation_id,
    reset_job_context,
    set_correlation_id,
    set_job_context,
)
from workflows.langgraph.outreach_generation.generation import OutreachDraftContent
from workflows.langgraph.outreach_generation.graph import build_graph

pytestmark = pytest.mark.asyncio


def _initial_state(*, job_id, user_id, contact, candidate_profile) -> dict:
    return {
        "job_id": job_id,
        "user_id": user_id,
        "contact": contact,
        "candidate_profile": candidate_profile,
        "selected_resume_id": ResumeId(uuid4()),
        "channel": None,
        "draft_message": None,
        "errors": [],
    }


async def _run_graph(state: dict, *, company: str, title: str) -> dict:
    """Drives the compiled graph the same way `outreach.consumers
    ._handle_contacts_found_async` does — via the context-var side channel,
    since `generate_message`/`persist_and_publish` depend on it
    (workflows/langgraph/outreach_generation/context.py)."""
    correlation_token = set_correlation_id(None)
    job_context_token = set_job_context(JobContext(company=company, title=title))
    try:
        return await build_graph().ainvoke(state)
    finally:
        reset_job_context(job_context_token)
        reset_correlation_id(correlation_token)


def _wire(session_factory) -> InMemoryBroker:
    broker = InMemoryBroker()
    set_session_factory(session_factory)
    set_event_producer(EventProducer("outreach-service", client=InMemoryProducerClient(broker)))
    return broker


# ---------------------------------------------------------------------------
# Scenario A — email
# ---------------------------------------------------------------------------


async def test_full_graph_email_generates_personalized_draft_pending_approval(
    session_factory,
) -> None:
    job_id, user_id = JobId(uuid4()), UserId(uuid4())
    # RankedContact (the locked OutreachGenerationState.contact shape) has
    # no `email` field — see select_channel's docstring for this
    # documented gap. A duck-typed double reaches the EMAIL branch through
    # the real graph, exactly as test_nodes.py's own select_channel email
    # test does at the unit level.
    base_contact = make_ranked_contact(profile_url=None, headline="Technical Recruiter at Acme AI")
    contact = SimpleNamespace(
        contact_id=base_contact.contact_id,
        full_name=base_contact.full_name,
        headline=base_contact.headline,
        contact_type=base_contact.contact_type,
        profile_url=None,
        email="jordan@example.com",
        relevance_score=base_contact.relevance_score,
    )
    profile = make_resume_profile(
        title="AI Engineer", skills=["Python", "LangGraph", "Kafka"]
    )
    broker = _wire(session_factory)
    nodes_module.set_llm_client(
        FakeLLMClient(
            default=OutreachDraftContent(
                subject="Regarding the AI Engineer opening",
                body="Hi Jordan, I'm reaching out about the AI Engineer role at Acme AI...",
            )
        )
    )

    state = _initial_state(job_id=job_id, user_id=user_id, contact=contact, candidate_profile=profile)
    final_state = await _run_graph(state, company="Acme AI", title="AI Engineer")

    assert final_state["channel"] == OutreachChannel.EMAIL
    assert final_state["draft_message"].startswith("Subject: Regarding the AI Engineer opening")

    messages = broker.log(Topic.OUTREACH_GENERATED.value)
    assert len(messages) == 1
    published = deserialize(Topic.OUTREACH_GENERATED, messages[0].value())
    assert published.payload.channel == OutreachChannel.EMAIL

    async with session_factory() as session:
        stored = await OutreachRepository(session).get_for_job(job_id)
    assert stored.status == OutreachStatus.PENDING_APPROVAL


# ---------------------------------------------------------------------------
# Scenario B — LinkedIn connection request
# ---------------------------------------------------------------------------


async def test_full_graph_linkedin_generates_short_connection_request(session_factory) -> None:
    job_id, user_id = JobId(uuid4()), UserId(uuid4())
    contact = make_ranked_contact(profile_url="https://example.com/in/jordan-smith")
    profile = make_resume_profile()
    broker = _wire(session_factory)
    nodes_module.set_llm_client(
        FakeLLMClient(default=OutreachDraftContent(body="Hi Jordan, " + "x" * 400))
    )

    state = _initial_state(job_id=job_id, user_id=user_id, contact=contact, candidate_profile=profile)
    final_state = await _run_graph(
        state, company="Acme Robotics", title="Senior Mechanical Design Engineer"
    )

    assert final_state["channel"] == OutreachChannel.LINKEDIN_CONNECTION_REQUEST
    assert len(final_state["draft_message"]) <= 300

    messages = broker.log(Topic.OUTREACH_GENERATED.value)
    assert len(messages) == 1

    async with session_factory() as session:
        stored = await OutreachRepository(session).get_for_job(job_id)
    assert stored.status == OutreachStatus.PENDING_APPROVAL
    assert stored.status != OutreachStatus.SENT


# ---------------------------------------------------------------------------
# Scenario C — cross-profession (software engineer / mechanical engineer /
# HR), same graph and node functions throughout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile_kwargs", "job_title", "job_company"),
    [
        (
            {"title": "Software Engineer", "skills": ["Python", "Kafka", "LangGraph"]},
            "Backend Software Engineer",
            "Acme Tech",
        ),
        (
            {"title": "Mechanical Design Engineer", "skills": ["CAD", "SolidWorks", "GD&T"]},
            "Senior Mechanical Design Engineer",
            "Acme Robotics",
        ),
        (
            {"title": "HR Business Partner", "skills": ["Talent Acquisition", "Employee Relations"]},
            "HR Business Partner",
            "Acme Corp",
        ),
    ],
    ids=["software-engineer", "mechanical-engineer", "hr-professional"],
)
async def test_full_graph_same_code_path_across_professions(
    session_factory, profile_kwargs: dict, job_title: str, job_company: str
) -> None:
    job_id, user_id = JobId(uuid4()), UserId(uuid4())
    contact = make_ranked_contact(profile_url="https://example.com/in/x")
    profile = make_resume_profile(**profile_kwargs)
    broker = _wire(session_factory)
    nodes_module.set_llm_client(
        FakeLLMClient(
            default=OutreachDraftContent(body=f"Hi, regarding the {job_title} role at {job_company}.")
        )
    )

    state = _initial_state(job_id=job_id, user_id=user_id, contact=contact, candidate_profile=profile)
    final_state = await _run_graph(state, company=job_company, title=job_title)

    assert final_state["draft_message"]
    assert len(broker.log(Topic.OUTREACH_GENERATED.value)) == 1

    async with session_factory() as session:
        stored = await OutreachRepository(session).get_for_job(job_id)
    assert stored.status == OutreachStatus.PENDING_APPROVAL
