"""Shared fixtures and sample-payload factories for Kafka infrastructure
tests.

Payload factories build a minimal valid instance of every canonical event
payload type from docs/architecture/event-contracts.md, keyed by Topic, so
tests can iterate over ``infrastructure.kafka.topics.all_specs()`` without
hand-writing ten near-identical payload literals per test.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import BaseModel

from infrastructure.kafka.in_memory import (
    InMemoryBroker,
    InMemoryConsumerClient,
    InMemoryProducerClient,
)
from infrastructure.kafka.topics import Topic
from shared.events.payloads import (
    ContactSearchRequest,
    OutreachDecision,
    OutreachSentConfirmation,
    ProfileUpdateSummary,
)
from shared.types.dto import (
    ApplicationStatusUpdate,
    ContactRankingResult,
    JobMatchResult,
    NormalizedJob,
    OutreachDraft,
)
from shared.types.enums import (
    ApplicationStatus,
    JobSourceType,
    MatchRecommendation,
    OutreachChannel,
    OutreachDecisionType,
    ProfileChangeType,
)


def _now() -> datetime:
    return datetime.now(UTC)


def make_normalized_job() -> NormalizedJob:
    return NormalizedJob(
        job_id=uuid4(),
        user_id=uuid4(),
        company="Acme Robotics",
        title="Senior Mechanical Engineer",
        description="Design and validate mechanical subsystems.",
        source_type=JobSourceType.MANUAL_URL,
        discovered_at=_now(),
    )


def make_job_match_result(
    recommendation: MatchRecommendation = MatchRecommendation.SHORTLIST,
) -> JobMatchResult:
    return JobMatchResult(
        job_match_id=uuid4(),
        job_id=uuid4(),
        user_id=uuid4(),
        selected_profile_id=uuid4(),
        selected_resume_id=uuid4(),
        match_score=0.92,
        recommendation=recommendation,
        matched_at=_now(),
    )


def make_profile_update_summary() -> ProfileUpdateSummary:
    return ProfileUpdateSummary(
        profile_id=uuid4(),
        user_id=uuid4(),
        resume_id=uuid4(),
        change_type=ProfileChangeType.CREATED,
        updated_at=_now(),
    )


def make_contact_search_request() -> ContactSearchRequest:
    return ContactSearchRequest(
        job_id=uuid4(),
        user_id=uuid4(),
        company="Acme Robotics",
        title="Senior Mechanical Engineer",
    )


def make_contact_ranking_result() -> ContactRankingResult:
    return ContactRankingResult(
        job_id=uuid4(),
        user_id=uuid4(),
        contacts=[],
        ranked_at=_now(),
    )


def make_outreach_draft() -> OutreachDraft:
    return OutreachDraft(
        outreach_id=uuid4(),
        job_id=uuid4(),
        contact_id=uuid4(),
        user_id=uuid4(),
        channel=OutreachChannel.EMAIL,
        draft_message="Hi, I'd love a referral for the role.",
        generated_at=_now(),
    )


def make_outreach_decision() -> OutreachDecision:
    return OutreachDecision(
        outreach_id=uuid4(),
        job_id=uuid4(),
        user_id=uuid4(),
        decision=OutreachDecisionType.APPROVED,
        decided_at=_now(),
        decided_by=uuid4(),
    )


def make_outreach_sent_confirmation() -> OutreachSentConfirmation:
    return OutreachSentConfirmation(
        outreach_id=uuid4(),
        job_id=uuid4(),
        user_id=uuid4(),
        channel=OutreachChannel.EMAIL,
        sent_at=_now(),
    )


def make_application_status_update() -> ApplicationStatusUpdate:
    return ApplicationStatusUpdate(
        application_id=uuid4(),
        job_id=uuid4(),
        user_id=uuid4(),
        new_status=ApplicationStatus.DISCOVERED,
        changed_at=_now(),
        triggered_by="tracking-service",
    )


SAMPLE_PAYLOAD_FACTORIES: dict[Topic, "type[BaseModel] | object"] = {
    Topic.JOBS_DISCOVERED: make_normalized_job,
    Topic.JOBS_MATCHED: make_job_match_result,
    Topic.JOBS_SHORTLISTED: lambda: make_job_match_result(MatchRecommendation.SHORTLIST),
    Topic.PROFILES_UPDATED: make_profile_update_summary,
    Topic.CONTACTS_REQUESTED: make_contact_search_request,
    Topic.CONTACTS_FOUND: make_contact_ranking_result,
    Topic.OUTREACH_GENERATED: make_outreach_draft,
    Topic.OUTREACH_APPROVED: make_outreach_decision,
    Topic.OUTREACH_SENT: make_outreach_sent_confirmation,
    Topic.APPLICATIONS_UPDATED: make_application_status_update,
}


def sample_payload(topic: Topic) -> BaseModel:
    return SAMPLE_PAYLOAD_FACTORIES[topic]()


@pytest.fixture
def broker() -> InMemoryBroker:
    return InMemoryBroker()


@pytest.fixture
def producer_client(broker: InMemoryBroker) -> InMemoryProducerClient:
    return InMemoryProducerClient(broker)


def make_consumer_client(broker: InMemoryBroker, group_id: str) -> InMemoryConsumerClient:
    return InMemoryConsumerClient(broker, group_id)
