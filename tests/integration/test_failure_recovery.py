"""Failure/recovery scenarios: each component's existing fakes/error types
are scripted to fail at a named point, asserting the documented failure
behavior (canonical `ErrorCode`, no partial/corrupted state, no false
downstream event).

A: resume parsing LLM failure -> Resume.status = PARSE_FAILED, no profile,
   no profiles.updated.
B: job ingestion page-fetch failure -> JOB_FETCH_FAILED, no Job row, no
   jobs.discovered.
C: every candidate profile's scoring LLM call fails -> JobMatch persisted
   with recommendation=IGNORE (score 0.0), jobs.matched published, but
   jobs.shortlisted/contacts.requested are not.
D: zero ACTIVE profiles for the user -> NO_PROFILES_AVAILABLE: no JobMatch
   row, no jobs.matched, Job.processing_status -> FAILED.
E: people-search provider failure -> CONTACT_SEARCH_FAILED, contacts.found
   still published with an empty contacts list (not an error state).
F: contact-ranking LLM failure -> rule-based fallback signals, contacts
   still ranked and published.
G: external send failure -> Outreach.status = SEND_FAILED, send_error set,
   no outreach.sent published.
H: outreach message-generation LLM failure -> no draft persisted or
   published (a human cannot approve a message that doesn't exist).
I: (most important) JobMatchRepository.add() succeeds but the subsequent
   jobs.matched publish fails -> exactly one JobMatch row with
   published_at IS NULL; redelivering the same jobs.discovered republishes
   without re-scoring and without a second row.
J: genuinely malformed bytes on a topic route to `<topic>.dlq` as raw bytes
   with a `failure_reason` header, via a real `EventConsumer` instance (not
   a handler called directly) — the transport-layer path per
   kafka-topics.md's "Malformed/undeserializable messages" section.

See docs/architecture/database-ownership.md#job_matches's "Publish-
reliability column" section, matching/consumers.py's three-way idempotency
check docstring, infrastructure/kafka/consumer.py's `_process_message`, and
the task brief's section 9.
"""

from __future__ import annotations

from uuid import UUID

import pytest

import matching.events as matching_events
from infrastructure.external.errors import MessageSendError, PeopleSearchRequestError
from infrastructure.external.message_send import (
    MessageSendClient,
)
from infrastructure.kafka.consumer import EventConsumer
from infrastructure.kafka.in_memory import (
    InMemoryConsumerClient,
    InMemoryProducerClient,
)
from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.topics import Topic
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from matching.errors import MatchingError
from matching.repository import JobMatchRepository
from outreach.errors import OutreachError
from shared.types.enums import ContactType, JobProcessingStatus, OutreachChannel
from tests.contacts.conftest import make_hit
from tests.integration.conftest import (
    ContactsFakeLLMClient,
    FakeUserPreferencesClient,
    IntegrationHarness,
    MatchingFakeLLMClient,
    OutreachFakeLLMClient,
    dispatch,
    llm_response,
    log_envelopes,
)
from tests.jobs.conftest import make_extracted_fields
from workflows.langgraph.contact_discovery.discovery import (
    ContactClassificationBatch,
    ContactSearchPlan,
    HitClassification,
    RelevanceSignals,
)
from workflows.langgraph.job_matching.scoring import ProfileScoringOutput
from workflows.langgraph.outreach_generation.generation import OutreachDraftContent

JOB_URL = "https://boards.example.com/jobs/failure-recovery-1"

_RESUME_TEXT = (
    "Jordan Rivera. Senior Mechanical Design Engineer. 7 years designing "
    "mechanical subsystems for industrial robots."
)
_PROFILE_FIELDS = {
    "title": "Senior Mechanical Design Engineer",
    "summary": "Mechanical engineer with 7 years designing robotic subsystems.",
    "skills": ["CAD", "SolidWorks", "GD&T", "DFM"],
    "experience_years": 7.0,
    "seniority": "Senior",
    "target_roles": ["Mechanical Design Engineer"],
}
_JOB_PAGE_TEXT = (
    "Senior Mechanical Design Engineer at Acme Robotics. Own CAD models "
    "from concept through DFM/DFA review."
)


def _job_extractor_fixture() -> dict:
    return {
        _JOB_PAGE_TEXT: make_extracted_fields(
            company="Acme Robotics",
            title="Senior Mechanical Design Engineer",
            location="Remote",
            description=_JOB_PAGE_TEXT,
            extracted_skills=["CAD", "SolidWorks", "GD&T", "DFM"],
            experience_required="5+ years",
        )
    }


def _good_score() -> ProfileScoringOutput:
    return ProfileScoringOutput(
        role_relevance=0.9,
        skills_fit=0.9,
        experience_fit=0.9,
        domain_fit=0.9,
        seniority_fit=0.9,
        location_preference_fit=0.5,
        overall_score=0.9,
        matched_skills=["CAD", "SolidWorks", "GD&T", "DFM"],
        missing_skills=[],
        reasoning="Strong fit.",
    )


# ---------------------------------------------------------------------------
# A. Resume parsing LLM failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_resume_parsing_llm_failure_marks_parse_failed(
    harness: IntegrationHarness,
) -> None:
    user = harness.create_user()
    harness.set_profiles_llm(
        [
            LLMProviderError(
                LLMFailureReason.INVALID_RESPONSE,
                "model returned unparseable text",
                provider="fake",
                model="fake",
            )
        ]
    )
    harness.upload_resume(user, "resume.txt", _RESUME_TEXT)

    resumes = harness.client.get("/resumes", headers=harness.auth_headers(user)).json()
    assert len(resumes) == 1
    assert resumes[0]["status"] == "PARSE_FAILED"

    profiles = harness.list_profiles(user)
    assert profiles == []


# ---------------------------------------------------------------------------
# B. Job ingestion page-fetch failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b_job_ingestion_fetch_failure_persists_nothing(
    harness: IntegrationHarness,
) -> None:
    user = harness.create_user()
    harness.set_job_ingestion_fakes(errors={JOB_URL: RuntimeError("network down")})

    response = harness.client.post(
        "/jobs/ingest-url", json={"url": JOB_URL}, headers=harness.auth_headers(user)
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "JOB_FETCH_FAILED"
    assert log_envelopes(harness.broker, Topic.JOBS_DISCOVERED) == []


# ---------------------------------------------------------------------------
# C. Every profile's scoring LLM call fails -> IGNORE, jobs.matched only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c_total_scoring_failure_ignores_but_still_publishes_jobs_matched(
    harness: IntegrationHarness,
) -> None:
    user = harness.create_user()
    harness.set_profiles_llm([llm_response(_PROFILE_FIELDS)])
    harness.upload_resume(user, "resume.txt", _RESUME_TEXT)
    harness.set_job_ingestion_fakes(
        pages={JOB_URL: _JOB_PAGE_TEXT}, extractor_by_content=_job_extractor_fixture()
    )
    job = harness.ingest_job(user, JOB_URL)

    harness.sync_matching_profiles(user)
    harness.set_matching_preferences(FakeUserPreferencesClient({}))
    harness.set_matching_llm(
        MatchingFakeLLMClient(
            default=LLMProviderError(
                LLMFailureReason.CONNECTION_ERROR,
                "llm unreachable",
                provider="fake",
                model="fake",
            )
        )
    )

    discovered = log_envelopes(harness.broker, Topic.JOBS_DISCOVERED)[0]
    await dispatch(Topic.JOBS_DISCOVERED, discovered)

    matched = log_envelopes(harness.broker, Topic.JOBS_MATCHED)
    assert len(matched) == 1
    assert matched[0].payload.match_score == 0.0
    assert matched[0].payload.recommendation.value == "IGNORE"
    assert log_envelopes(harness.broker, Topic.JOBS_SHORTLISTED) == []
    assert log_envelopes(harness.broker, Topic.CONTACTS_REQUESTED) == []

    job_response = harness.client.get(f"/jobs/{job['id']}").json()
    assert job_response["processing_status"] == "MATCHED"


# ---------------------------------------------------------------------------
# D. Zero ACTIVE profiles -> NO_PROFILES_AVAILABLE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_d_no_active_profiles_marks_job_failed_and_publishes_nothing(
    harness: IntegrationHarness,
) -> None:
    user = harness.create_user()
    # No resume uploaded at all -> GET /profiles returns [].
    harness.set_job_ingestion_fakes(
        pages={JOB_URL: _JOB_PAGE_TEXT}, extractor_by_content=_job_extractor_fixture()
    )
    job = harness.ingest_job(user, JOB_URL)

    harness.sync_matching_profiles(user)  # empty profile list, real API round-trip
    harness.set_matching_preferences(FakeUserPreferencesClient({}))
    harness.set_matching_llm(MatchingFakeLLMClient(default=_good_score()))

    discovered = log_envelopes(harness.broker, Topic.JOBS_DISCOVERED)[0]
    await dispatch(Topic.JOBS_DISCOVERED, discovered)

    assert log_envelopes(harness.broker, Topic.JOBS_MATCHED) == []
    async with harness.session_factory() as session:
        match = await JobMatchRepository(session).get_latest_for_job(UUID(job["id"]))
    assert match is None

    job_response = harness.client.get(f"/jobs/{job['id']}").json()
    assert job_response["processing_status"] == JobProcessingStatus.FAILED.value


# ---------------------------------------------------------------------------
# E. People-search provider failure -> empty contacts.found, not an error
# ---------------------------------------------------------------------------


async def _drive_to_contacts_requested(harness: IntegrationHarness) -> tuple[str, dict]:
    user = harness.create_user()
    harness.set_profiles_llm([llm_response(_PROFILE_FIELDS)])
    harness.upload_resume(user, "resume.txt", _RESUME_TEXT)
    harness.set_job_ingestion_fakes(
        pages={JOB_URL: _JOB_PAGE_TEXT}, extractor_by_content=_job_extractor_fixture()
    )
    job = harness.ingest_job(user, JOB_URL)
    harness.sync_matching_profiles(user)
    harness.set_matching_preferences(FakeUserPreferencesClient({}))
    harness.set_matching_llm(MatchingFakeLLMClient(default=_good_score()))

    discovered = log_envelopes(harness.broker, Topic.JOBS_DISCOVERED)[0]
    await dispatch(Topic.JOBS_DISCOVERED, discovered)
    matched = log_envelopes(harness.broker, Topic.JOBS_MATCHED)[0]
    await dispatch(Topic.JOBS_MATCHED, matched)
    shortlisted = log_envelopes(harness.broker, Topic.JOBS_SHORTLISTED)[0]
    await dispatch(Topic.JOBS_SHORTLISTED, shortlisted)
    return job["id"], user


@pytest.mark.asyncio
async def test_e_people_search_failure_still_publishes_empty_contacts_found(
    harness: IntegrationHarness,
) -> None:
    job_id, _user = await _drive_to_contacts_requested(harness)

    import workflows.langgraph.contact_discovery.nodes as contact_nodes
    from tests.contacts.conftest import FakePeopleSearchClient

    contact_nodes.set_people_search_client(
        FakePeopleSearchClient(error=PeopleSearchRequestError("provider down"))
    )
    contact_nodes.set_llm_client(
        ContactsFakeLLMClient(
            default=ContactSearchPlan(role_keywords=["Engineering Manager"])
        )
    )

    contacts_requested = log_envelopes(harness.broker, Topic.CONTACTS_REQUESTED)[0]
    await dispatch(Topic.CONTACTS_REQUESTED, contacts_requested)

    found = log_envelopes(harness.broker, Topic.CONTACTS_FOUND)
    assert len(found) == 1
    assert found[0].payload.contacts == []

    contacts_api = harness.client.get(f"/jobs/{job_id}/contacts").json()
    assert contacts_api == []


# ---------------------------------------------------------------------------
# F. Contact ranking LLM failure -> rule-based fallback, still succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f_ranking_llm_failure_falls_back_to_rule_based_scoring(
    harness: IntegrationHarness,
) -> None:
    await _drive_to_contacts_requested(harness)

    hit = make_hit(
        full_name="Sam Lee",
        headline="Engineering Manager, Mechanical at Acme Robotics",
        company="Acme Robotics",
        profile_url="https://example.com/in/sam-lee",
    )
    harness.set_contacts_fakes(
        hits=[hit],
        llm=ContactsFakeLLMClient(
            results={
                "Propose 3-6 short search": ContactSearchPlan(role_keywords=["Manager"]),
                "Classify each of the following": ContactClassificationBatch(
                    classifications=[
                        HitClassification(index=0, contact_type=ContactType.HIRING_MANAGER)
                    ]
                ),
                # No entry for the ranking prompt (keyed by "Sam Lee") ->
                # FakeLLMClient's `default` is used instead.
            },
            default=LLMProviderError(
                LLMFailureReason.CONNECTION_ERROR,
                "llm unreachable during ranking",
                provider="fake",
                model="fake",
            ),
        ),
    )

    contacts_requested = log_envelopes(harness.broker, Topic.CONTACTS_REQUESTED)[0]
    await dispatch(Topic.CONTACTS_REQUESTED, contacts_requested)

    found = log_envelopes(harness.broker, Topic.CONTACTS_FOUND)
    assert len(found) == 1
    assert len(found[0].payload.contacts) == 1
    assert found[0].payload.contacts[0].full_name == "Sam Lee"
    # Rule-based fallback still produces a real relevance_score, not zero.
    assert found[0].payload.contacts[0].relevance_score > 0.0


# ---------------------------------------------------------------------------
# G. External send failure -> SEND_FAILED, no outreach.sent
# ---------------------------------------------------------------------------


async def _drive_to_outreach_generated_for_failure_tests(
    harness: IntegrationHarness,
) -> tuple[str, str]:
    job_id, _user = await _drive_to_contacts_requested(harness)
    hit = make_hit(
        full_name="Sam Lee",
        headline="Engineering Manager, Mechanical at Acme Robotics",
        company="Acme Robotics",
        profile_url="https://example.com/in/sam-lee",
    )
    harness.set_contacts_fakes(
        hits=[hit],
        llm=ContactsFakeLLMClient(
            results={
                "Propose 3-6 short search": ContactSearchPlan(role_keywords=["Manager"]),
                "Classify each of the following": ContactClassificationBatch(
                    classifications=[
                        HitClassification(index=0, contact_type=ContactType.HIRING_MANAGER)
                    ]
                ),
                "Sam Lee": RelevanceSignals(
                    role_similarity=0.9, department_relevance=0.9, seniority_fit=0.8
                ),
            }
        ),
    )
    contacts_requested = log_envelopes(harness.broker, Topic.CONTACTS_REQUESTED)[0]
    await dispatch(Topic.CONTACTS_REQUESTED, contacts_requested)
    contacts_found = log_envelopes(harness.broker, Topic.CONTACTS_FOUND)[0]

    harness.sync_outreach_clients(job_id)
    harness.set_outreach_llm(
        OutreachFakeLLMClient(default=OutreachDraftContent(body="Hi Sam, ..."))
    )
    await dispatch(Topic.CONTACTS_FOUND, contacts_found)
    generated = log_envelopes(harness.broker, Topic.OUTREACH_GENERATED)[0]
    return job_id, str(generated.payload.outreach_id)


@pytest.mark.asyncio
async def test_g_external_send_failure_marks_send_failed_no_outreach_sent(
    harness: IntegrationHarness,
) -> None:
    import outreach.consumers as outreach_consumers

    _job_id, outreach_id = await _drive_to_outreach_generated_for_failure_tests(harness)

    class _FailingProvider:
        name = "failing"

        async def send(self, message, *, timeout_seconds: float) -> str:
            raise MessageSendError("provider rejected the message")

    failing_client = MessageSendClient({channel: _FailingProvider() for channel in OutreachChannel})
    outreach_consumers.set_message_send_client(failing_client)

    harness.approve_outreach(outreach_id)
    approved = log_envelopes(harness.broker, Topic.OUTREACH_APPROVED)[0]

    with pytest.raises(OutreachError):
        await outreach_consumers._handle_outreach_approved_async(approved)

    assert log_envelopes(harness.broker, Topic.OUTREACH_SENT) == []
    outreach_after = harness.client.get(f"/outreach/{outreach_id}").json()
    assert outreach_after["status"] == "SEND_FAILED"


# ---------------------------------------------------------------------------
# H. Outreach message-generation LLM failure -> nothing persisted/published
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h_message_generation_failure_persists_and_publishes_nothing(
    harness: IntegrationHarness,
) -> None:
    job_id, user = await _drive_to_contacts_requested(harness)
    hit = make_hit(
        full_name="Sam Lee",
        headline="Engineering Manager, Mechanical at Acme Robotics",
        company="Acme Robotics",
        profile_url="https://example.com/in/sam-lee",
    )
    harness.set_contacts_fakes(
        hits=[hit],
        llm=ContactsFakeLLMClient(
            results={
                "Propose 3-6 short search": ContactSearchPlan(role_keywords=["Manager"]),
                "Classify each of the following": ContactClassificationBatch(
                    classifications=[
                        HitClassification(index=0, contact_type=ContactType.HIRING_MANAGER)
                    ]
                ),
                "Sam Lee": RelevanceSignals(
                    role_similarity=0.9, department_relevance=0.9, seniority_fit=0.8
                ),
            }
        ),
    )
    contacts_requested = log_envelopes(harness.broker, Topic.CONTACTS_REQUESTED)[0]
    await dispatch(Topic.CONTACTS_REQUESTED, contacts_requested)
    contacts_found = log_envelopes(harness.broker, Topic.CONTACTS_FOUND)[0]

    harness.sync_outreach_clients(job_id)
    harness.set_outreach_llm(
        OutreachFakeLLMClient(
            default=LLMProviderError(
                LLMFailureReason.CONNECTION_ERROR,
                "llm unreachable during generation",
                provider="fake",
                model="fake",
            )
        )
    )

    import outreach.consumers as outreach_consumers

    with pytest.raises(OutreachError):
        await outreach_consumers._handle_contacts_found_async(contacts_found)

    assert log_envelopes(harness.broker, Topic.OUTREACH_GENERATED) == []
    outreach_list = harness.client.get("/outreach", headers=harness.auth_headers(user)).json()
    assert outreach_list == []


# ---------------------------------------------------------------------------
# I. Publish-reliability gap: JobMatch persisted, publish fails, then
#    redelivery republishes without re-scoring and without a second row.
# ---------------------------------------------------------------------------


class _FlakyProducerClient:
    """Wraps a real `ProducerClient`, raising once for the first `produce()`
    call on a topic in `fail_once_for`, then delegating normally forever
    after (including for that same topic on a later attempt).
    """

    def __init__(self, inner, fail_once_for: set[str]) -> None:
        self._inner = inner
        self._fail_once_for = set(fail_once_for)

    def produce(self, topic, value=None, key=None, headers=None):
        if topic in self._fail_once_for:
            self._fail_once_for.discard(topic)
            raise RuntimeError(f"simulated broker outage publishing to {topic}")
        self._inner.produce(topic, value=value, key=key, headers=headers)

    def poll(self, timeout: float) -> int:
        return self._inner.poll(timeout)

    def flush(self, timeout: float) -> int:
        return self._inner.flush(timeout)


@pytest.mark.asyncio
async def test_i_publish_failure_after_persist_recovers_without_rescoring(
    harness: IntegrationHarness,
) -> None:
    user = harness.create_user()
    harness.set_profiles_llm([llm_response(_PROFILE_FIELDS)])
    harness.upload_resume(user, "resume.txt", _RESUME_TEXT)
    harness.set_job_ingestion_fakes(
        pages={JOB_URL: _JOB_PAGE_TEXT}, extractor_by_content=_job_extractor_fixture()
    )
    job = harness.ingest_job(user, JOB_URL)
    job_id = job["id"]

    harness.sync_matching_profiles(user)
    harness.set_matching_preferences(FakeUserPreferencesClient({}))
    matching_llm = MatchingFakeLLMClient(default=_good_score())
    harness.set_matching_llm(matching_llm)

    flaky_client = _FlakyProducerClient(
        InMemoryProducerClient(harness.broker), fail_once_for={Topic.JOBS_MATCHED.value}
    )
    matching_events.set_event_producer(
        EventProducer(matching_events.PRODUCER_NAME, client=flaky_client)
    )

    discovered = log_envelopes(harness.broker, Topic.JOBS_DISCOVERED)[0]
    with pytest.raises(MatchingError):
        await dispatch(Topic.JOBS_DISCOVERED, discovered)

    assert len(matching_llm.prompts) == 1
    assert log_envelopes(harness.broker, Topic.JOBS_MATCHED) == []

    async with harness.session_factory() as session:
        result = await JobMatchRepository(session).get_latest_for_job_with_published_at(
            UUID(job_id)
        )
    assert result is not None
    job_match, published_at = result
    assert published_at is None

    # Restore a healthy producer (the flaky client already consumed its one
    # scripted failure) and redeliver the *same* jobs.discovered message —
    # simulating the operator republishing a DLQ'd message.
    matching_events.set_event_producer(
        EventProducer(
            matching_events.PRODUCER_NAME, client=InMemoryProducerClient(harness.broker)
        )
    )
    await dispatch(Topic.JOBS_DISCOVERED, discovered)

    # No re-scoring: still exactly one prompt sent, ever.
    assert len(matching_llm.prompts) == 1
    # Republished exactly once.
    assert len(log_envelopes(harness.broker, Topic.JOBS_MATCHED)) == 1

    async with harness.session_factory() as session:
        result_after = await JobMatchRepository(
            session
        ).get_latest_for_job_with_published_at(UUID(job_id))
    assert result_after is not None
    job_match_after, published_at_after = result_after
    assert published_at_after is not None
    # Still exactly one JobMatch row for this job — same id as before.
    assert job_match_after.id == job_match.id


# ---------------------------------------------------------------------------
# J. Malformed bytes route to <topic>.dlq with a failure_reason header
# ---------------------------------------------------------------------------


def test_j_malformed_message_routes_to_dlq_as_raw_bytes(
    harness: IntegrationHarness,
) -> None:
    group_id = "job-matching-service"
    consumer_client = InMemoryConsumerClient(harness.broker, group_id)
    dlq_producer = EventProducer(
        group_id, client=InMemoryProducerClient(harness.broker)
    )

    def _unreachable_handler(envelope) -> None:  # pragma: no cover - never called
        raise AssertionError("handler must not run for malformed bytes")

    consumer = EventConsumer(
        Topic.JOBS_DISCOVERED,
        group_id,
        _unreachable_handler,
        client=consumer_client,
        dlq_producer=dlq_producer,
    )

    bad_bytes = b"not even close to a valid EventEnvelope JSON payload"
    harness.broker.append(Topic.JOBS_DISCOVERED.value, bad_bytes, None, None)

    handled = consumer.poll_once(timeout=0.01)
    assert handled is True

    dlq_topic = f"{Topic.JOBS_DISCOVERED.value}.dlq"
    dlq_messages = harness.broker.log(dlq_topic)
    assert len(dlq_messages) == 1
    assert dlq_messages[0].value() == bad_bytes  # preserved verbatim

    headers = dict(dlq_messages[0].headers() or [])
    assert "failure_reason" in headers
    assert isinstance(headers["failure_reason"], bytes)
