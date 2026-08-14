"""docs/architecture/kafka-topics.md: exactly ten topics, correctly keyed."""

from infrastructure.kafka.topics import Topic, all_topic_names, dlq_topic, spec_for

EXPECTED_TOPIC_NAMES = {
    "jobs.discovered",
    "jobs.matched",
    "jobs.shortlisted",
    "profiles.updated",
    "contacts.requested",
    "contacts.found",
    "outreach.generated",
    "outreach.approved",
    "outreach.sent",
    "applications.updated",
}

# Partition key field per topic, exactly as documented in kafka-topics.md's
# per-topic tables.
EXPECTED_PARTITION_KEY_FIELDS = {
    Topic.JOBS_DISCOVERED: "job_id",
    Topic.JOBS_MATCHED: "job_id",
    Topic.JOBS_SHORTLISTED: "job_id",
    Topic.PROFILES_UPDATED: "user_id",
    Topic.CONTACTS_REQUESTED: "job_id",
    Topic.CONTACTS_FOUND: "job_id",
    Topic.OUTREACH_GENERATED: "job_id",
    Topic.OUTREACH_APPROVED: "job_id",
    Topic.OUTREACH_SENT: "job_id",
    Topic.APPLICATIONS_UPDATED: "application_id",
}


def test_exactly_ten_canonical_topics() -> None:
    assert {t.value for t in Topic} == EXPECTED_TOPIC_NAMES
    assert len(Topic) == 10


def test_all_topic_names_matches_enum() -> None:
    assert set(all_topic_names()) == EXPECTED_TOPIC_NAMES


def test_dlq_topic_naming_convention() -> None:
    for topic in Topic:
        assert dlq_topic(topic) == f"{topic.value}.dlq"


def test_all_topic_names_include_dlq() -> None:
    names = all_topic_names(include_dlq=True)
    for topic in Topic:
        assert topic.value in names
        assert f"{topic.value}.dlq" in names


def test_partition_key_field_matches_kafka_topics_doc() -> None:
    for topic, expected_field in EXPECTED_PARTITION_KEY_FIELDS.items():
        assert spec_for(topic).partition_key_field == expected_field
