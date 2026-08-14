"""Canonical Kafka topic names and their per-topic transport spec.

Locked contract — see docs/architecture/kafka-topics.md. These ten topics are
the complete set. Adding a topic is an architecture change (a new
``EventType`` member, a payload type in event-contracts.md, and a topic entry
in kafka-topics.md), never a local decision.

Components import ``Topic`` and the registry lookups below rather than
writing topic name string literals.
"""

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel

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
from shared.types.enums import EventType

DLQ_SUFFIX = ".dlq"


class Topic(str, Enum):
    """The ten canonical topics from kafka-topics.md."""

    JOBS_DISCOVERED = "jobs.discovered"
    JOBS_MATCHED = "jobs.matched"
    JOBS_SHORTLISTED = "jobs.shortlisted"
    PROFILES_UPDATED = "profiles.updated"
    CONTACTS_REQUESTED = "contacts.requested"
    CONTACTS_FOUND = "contacts.found"
    OUTREACH_GENERATED = "outreach.generated"
    OUTREACH_APPROVED = "outreach.approved"
    OUTREACH_SENT = "outreach.sent"
    APPLICATIONS_UPDATED = "applications.updated"


@dataclass(frozen=True)
class TopicSpec:
    """Everything the transport layer needs to know about one topic."""

    topic: Topic
    event_type: EventType
    payload_type: type[BaseModel]
    partition_key_field: str


_SPECS: tuple[TopicSpec, ...] = (
    TopicSpec(Topic.JOBS_DISCOVERED, EventType.JOB_DISCOVERED, NormalizedJob, "job_id"),
    TopicSpec(Topic.JOBS_MATCHED, EventType.JOB_MATCHED, JobMatchResult, "job_id"),
    TopicSpec(
        Topic.JOBS_SHORTLISTED, EventType.JOB_SHORTLISTED, JobMatchResult, "job_id"
    ),
    TopicSpec(
        Topic.PROFILES_UPDATED,
        EventType.PROFILE_UPDATED,
        ProfileUpdateSummary,
        "user_id",
    ),
    TopicSpec(
        Topic.CONTACTS_REQUESTED,
        EventType.CONTACTS_REQUESTED,
        ContactSearchRequest,
        "job_id",
    ),
    TopicSpec(
        Topic.CONTACTS_FOUND, EventType.CONTACTS_FOUND, ContactRankingResult, "job_id"
    ),
    TopicSpec(
        Topic.OUTREACH_GENERATED,
        EventType.OUTREACH_GENERATED,
        OutreachDraft,
        "job_id",
    ),
    TopicSpec(
        Topic.OUTREACH_APPROVED,
        EventType.OUTREACH_APPROVED,
        OutreachDecision,
        "job_id",
    ),
    TopicSpec(
        Topic.OUTREACH_SENT,
        EventType.OUTREACH_SENT,
        OutreachSentConfirmation,
        "job_id",
    ),
    TopicSpec(
        Topic.APPLICATIONS_UPDATED,
        EventType.APPLICATION_UPDATED,
        ApplicationStatusUpdate,
        "application_id",
    ),
)

_BY_TOPIC: dict[Topic, TopicSpec] = {spec.topic: spec for spec in _SPECS}


def spec_for(topic: Topic) -> TopicSpec:
    return _BY_TOPIC[topic]


def all_specs() -> tuple[TopicSpec, ...]:
    return _SPECS


def dlq_topic(topic: Topic) -> str:
    """The dead-letter topic for ``topic`` — ``<topic>.dlq``."""
    return f"{topic.value}{DLQ_SUFFIX}"


def all_topic_names(*, include_dlq: bool = False) -> tuple[str, ...]:
    names = [spec.topic.value for spec in _SPECS]
    if include_dlq:
        names.extend(dlq_topic(spec.topic) for spec in _SPECS)
    return tuple(names)


__all__ = [
    "DLQ_SUFFIX",
    "Topic",
    "TopicSpec",
    "all_specs",
    "all_topic_names",
    "dlq_topic",
    "spec_for",
]
