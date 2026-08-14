"""Kafka producer boundary for Job Ingestion Service.

Produces: `jobs.discovered` (JobDiscoveredEvent, payload NormalizedJob,
source_type=MANUAL_URL) — see
docs/architecture/event-contracts.md#jobdiscoveredevent and
docs/architecture/kafka-topics.md#jobsdiscovered.

Components never build an `EventEnvelope` or partition key themselves —
`infrastructure.kafka.producer.EventProducer` does that; this module only
supplies the topic and the `source` metadata tag for this producer.
"""

from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.topics import Topic
from shared.events.envelope import EventEnvelope
from shared.types.dto import NormalizedJob
from shared.types.ids import CorrelationId

PRODUCER_NAME = "job-ingestion-service"
SOURCE_TAG = "manual"


def publish_job_discovered(
    payload: NormalizedJob,
    *,
    producer: EventProducer,
    correlation_id: CorrelationId | None = None,
) -> EventEnvelope[NormalizedJob]:
    """Publish `payload` on `jobs.discovered`, tagged `source=manual`.

    `correlation_id` is left `None` for the manual path: a pasted URL is
    always the origin of a new causal chain (there is no upstream event to
    propagate an id from) — `EventProducer.publish` mints a fresh one.
    """
    return producer.publish(
        Topic.JOBS_DISCOVERED,
        payload,
        correlation_id=correlation_id,
        source=SOURCE_TAG,
    )


__all__ = ["PRODUCER_NAME", "SOURCE_TAG", "publish_job_discovered"]
