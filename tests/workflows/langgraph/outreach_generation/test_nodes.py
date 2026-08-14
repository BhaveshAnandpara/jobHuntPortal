"""Unit tests for the three OutreachGenerationState node functions
(docs/architecture/langgraph-state.md#outreachgenerationstate).

Covers task scenarios A (email), B (LinkedIn — shorter format), C
(cross-profession: software engineer / mechanical engineer / HR — same
code path), H (LLM failure), plus select_channel's abort path and
persist_and_publish's persistence/publish behavior.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

import workflows.langgraph.outreach_generation.nodes as nodes_module
from infrastructure.llm import LLMFailureReason, LLMProviderError
from outreach.errors import OutreachError
from outreach.repository import OutreachRepository
from shared.errors.codes import ErrorCode
from shared.types.enums import OutreachChannel, OutreachStatus
from shared.types.ids import JobId, ResumeId, UserId
from tests.outreach.conftest import (
    FakeLLMClient,
    make_ranked_contact,
    make_resume_profile,
)
from workflows.langgraph.outreach_generation.context import (
    JobContext,
    reset_job_context,
    set_job_context,
)
from workflows.langgraph.outreach_generation.generation import (
    LINKEDIN_CONNECTION_REQUEST_CHAR_LIMIT,
    OutreachDraftContent,
)
from workflows.langgraph.outreach_generation.nodes import (
    generate_message,
    persist_and_publish,
    select_channel,
)

pytestmark = pytest.mark.asyncio


def _initial_state(**overrides: object) -> dict:
    fields: dict[str, object] = {
        "job_id": JobId(uuid4()),
        "user_id": UserId(uuid4()),
        "contact": make_ranked_contact(),
        "candidate_profile": make_resume_profile(),
        "selected_resume_id": ResumeId(uuid4()),
        "channel": None,
        "draft_message": None,
        "errors": [],
    }
    fields.update(overrides)
    return fields


async def _generate_with_context(
    state: dict, *, company: str = "Acme Robotics", title: str = "Senior Mechanical Design Engineer"
) -> dict:
    token = set_job_context(JobContext(company=company, title=title))
    try:
        return await generate_message(state)
    finally:
        reset_job_context(token)


# ---------------------------------------------------------------------------
# select_channel
# ---------------------------------------------------------------------------


async def test_select_channel_prefers_linkedin_connection_request_when_profile_url_present() -> None:
    contact = make_ranked_contact(profile_url="https://example.com/in/jordan-smith")
    result = await select_channel(_initial_state(contact=contact))
    assert result["channel"] == OutreachChannel.LINKEDIN_CONNECTION_REQUEST


async def test_select_channel_uses_email_when_profile_url_absent_but_email_present() -> None:
    """`RankedContact` (the locked `OutreachGenerationState.contact` shape,
    shared-types.md#contactrankingresult) carries no `email` field — see
    `select_channel`'s docstring for this documented architecture gap.
    This test exercises the email-fallback branch's own logic directly
    with a duck-typed double, proving it is correct in case `RankedContact`
    gains an additive `email` field in the future (the real type ignores
    unknown constructor kwargs, so it cannot carry `email` today)."""
    contact = SimpleNamespace(
        contact_id=uuid4(),
        full_name="Jordan Smith",
        headline="Recruiter at Acme",
        contact_type=None,
        profile_url=None,
        email="jordan@example.com",
        relevance_score=5.0,
    )
    result = await select_channel(_initial_state(contact=contact))
    assert result["channel"] == OutreachChannel.EMAIL


async def test_select_channel_aborts_with_no_reachable_channel() -> None:
    contact = make_ranked_contact(profile_url=None)
    with pytest.raises(OutreachError) as exc_info:
        await select_channel(_initial_state(contact=contact))
    assert exc_info.value.error_code == ErrorCode.OUTREACH_GENERATION_FAILED


# ---------------------------------------------------------------------------
# generate_message — Scenario A: email
# ---------------------------------------------------------------------------


async def test_generate_message_email_produces_personalized_draft_with_subject() -> None:
    contact = make_ranked_contact(profile_url=None, headline="Technical Recruiter at Acme AI")
    profile = make_resume_profile(
        title="AI Engineer", skills=["Python", "LangGraph", "Kafka"], summary="AI engineer."
    )
    nodes_module.set_llm_client(
        FakeLLMClient(
            default=OutreachDraftContent(
                subject="Referral question re: AI Engineer role at Acme AI",
                body="Hi Jordan, I saw your work leading AI hiring at Acme AI...",
            )
        )
    )
    state = _initial_state(
        contact=contact, candidate_profile=profile, channel=OutreachChannel.EMAIL
    )

    result = await _generate_with_context(state, company="Acme AI", title="AI Engineer")

    assert result["draft_message"].startswith(
        "Subject: Referral question re: AI Engineer role at Acme AI"
    )
    assert "Hi Jordan" in result["draft_message"]


# ---------------------------------------------------------------------------
# generate_message — Scenario B: LinkedIn connection request (shorter
# format, real character-limit respected)
# ---------------------------------------------------------------------------


async def test_generate_message_linkedin_connection_request_respects_char_limit() -> None:
    contact = make_ranked_contact(profile_url="https://example.com/in/jordan-smith")
    profile = make_resume_profile()
    long_body = "Hi Jordan, " + ("x" * 500)
    nodes_module.set_llm_client(FakeLLMClient(default=OutreachDraftContent(body=long_body)))
    state = _initial_state(
        contact=contact,
        candidate_profile=profile,
        channel=OutreachChannel.LINKEDIN_CONNECTION_REQUEST,
    )

    result = await _generate_with_context(state)

    assert len(result["draft_message"]) <= LINKEDIN_CONNECTION_REQUEST_CHAR_LIMIT
    assert "Subject:" not in result["draft_message"]


async def test_generate_message_prompt_includes_channel_specific_instructions() -> None:
    contact = make_ranked_contact(profile_url="https://example.com/in/jordan-smith")
    profile = make_resume_profile()
    llm = FakeLLMClient(default=OutreachDraftContent(body="Hi Jordan, ..."))
    nodes_module.set_llm_client(llm)
    state = _initial_state(
        contact=contact,
        candidate_profile=profile,
        channel=OutreachChannel.LINKEDIN_CONNECTION_REQUEST,
    )

    await _generate_with_context(state)

    assert "character limit" in llm.prompts[-1]
    assert "connection request" in llm.prompts[-1]


# ---------------------------------------------------------------------------
# generate_message — Scenario C: cross-profession, same code path
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
async def test_generate_message_same_node_across_professions(
    profile_kwargs: dict, job_title: str, job_company: str
) -> None:
    """No hard-coded profession branch: the identical `generate_message`
    function and prompt template run for every profession, and the
    resulting prompt is grounded in that run's own job/candidate data
    (about_project.md's profession-independence requirement)."""
    contact = make_ranked_contact(profile_url="https://example.com/in/x")
    profile = make_resume_profile(**profile_kwargs)
    llm = FakeLLMClient(
        default=OutreachDraftContent(body=f"Hi, regarding the {job_title} role at {job_company}.")
    )
    nodes_module.set_llm_client(llm)
    state = _initial_state(
        contact=contact,
        candidate_profile=profile,
        channel=OutreachChannel.LINKEDIN_CONNECTION_REQUEST,
    )

    result = await _generate_with_context(state, company=job_company, title=job_title)

    prompt = llm.prompts[-1]
    assert job_title in prompt
    assert job_company in prompt
    assert profile_kwargs["title"] in prompt
    assert result["draft_message"]


# ---------------------------------------------------------------------------
# generate_message — abort paths
# ---------------------------------------------------------------------------


async def test_generate_message_aborts_when_channel_missing() -> None:
    state = _initial_state(channel=None)
    with pytest.raises(OutreachError) as exc_info:
        await _generate_with_context(state)
    assert exc_info.value.error_code == ErrorCode.OUTREACH_GENERATION_FAILED


async def test_generate_message_aborts_when_job_context_missing() -> None:
    state = _initial_state(channel=OutreachChannel.LINKEDIN_CONNECTION_REQUEST)
    with pytest.raises(OutreachError) as exc_info:
        await generate_message(state)  # no set_job_context around this call
    assert exc_info.value.error_code == ErrorCode.OUTREACH_GENERATION_FAILED


async def test_generate_message_llm_failure_raises_llm_provider_error() -> None:
    """Scenario H: LLM failure -> LLM_PROVIDER_ERROR; the exception aborts
    the node, so no draft_message is ever produced."""
    error = LLMProviderError(
        LLMFailureReason.TIMEOUT, "timed out", provider="fake", model="fake-model"
    )
    nodes_module.set_llm_client(FakeLLMClient(default=error))
    state = _initial_state(channel=OutreachChannel.LINKEDIN_CONNECTION_REQUEST)

    with pytest.raises(OutreachError) as exc_info:
        await _generate_with_context(state)

    assert exc_info.value.error_code == ErrorCode.LLM_PROVIDER_ERROR


# ---------------------------------------------------------------------------
# persist_and_publish
# ---------------------------------------------------------------------------


def _wire_persistence(session_factory, broker) -> None:
    from infrastructure.kafka.in_memory import InMemoryProducerClient
    from infrastructure.kafka.producer import EventProducer
    from outreach.db import set_session_factory
    from outreach.events import set_event_producer

    set_session_factory(session_factory)
    set_event_producer(EventProducer("outreach-service", client=InMemoryProducerClient(broker)))


async def test_persist_and_publish_creates_pending_approval_row_and_publishes(
    session_factory,
) -> None:
    from infrastructure.kafka.in_memory import InMemoryBroker
    from infrastructure.kafka.serialization import deserialize
    from infrastructure.kafka.topics import Topic

    broker = InMemoryBroker()
    _wire_persistence(session_factory, broker)

    job_id = JobId(uuid4())
    contact = make_ranked_contact(profile_url="https://example.com/in/jordan-smith")
    state = _initial_state(
        job_id=job_id,
        contact=contact,
        channel=OutreachChannel.LINKEDIN_CONNECTION_REQUEST,
        draft_message="Hi Jordan, I'd love to connect about the role.",
    )

    await persist_and_publish(state)

    async with session_factory() as session:
        stored = await OutreachRepository(session).get_for_job(job_id)
        assert stored is not None
        assert stored.status == OutreachStatus.PENDING_APPROVAL
        assert stored.draft_message == "Hi Jordan, I'd love to connect about the role."
        address = await OutreachRepository(session).get_recipient_address(stored.id)
        assert address == contact.profile_url

    messages = broker.log(Topic.OUTREACH_GENERATED.value)
    assert len(messages) == 1
    published = deserialize(Topic.OUTREACH_GENERATED, messages[0].value())
    assert published.payload.job_id == job_id
    assert published.payload.channel == OutreachChannel.LINKEDIN_CONNECTION_REQUEST


async def test_persist_and_publish_raises_when_channel_or_draft_missing() -> None:
    state = _initial_state(channel=None, draft_message=None)
    with pytest.raises(OutreachError):
        await persist_and_publish(state)


async def test_persist_and_publish_raises_on_db_failure(session_factory, monkeypatch) -> None:
    from infrastructure.kafka.in_memory import InMemoryBroker

    broker = InMemoryBroker()
    _wire_persistence(session_factory, broker)

    async def _broken_add(self, outreach, recipient_address=None):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(OutreachRepository, "add", _broken_add)

    state = _initial_state(channel=OutreachChannel.LINKEDIN_CONNECTION_REQUEST, draft_message="hi")
    with pytest.raises(OutreachError):
        await persist_and_publish(state)


async def test_persist_and_publish_raises_on_publish_failure(session_factory) -> None:
    from outreach.db import set_session_factory
    from outreach.events import set_event_producer

    set_session_factory(session_factory)

    class BrokenProducer:
        def publish(self, *args, **kwargs):
            raise RuntimeError("kafka broker unreachable")

    set_event_producer(BrokenProducer())

    state = _initial_state(channel=OutreachChannel.LINKEDIN_CONNECTION_REQUEST, draft_message="hi")
    with pytest.raises(OutreachError):
        await persist_and_publish(state)
