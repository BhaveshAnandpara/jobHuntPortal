"""Kafka producer boundary for Job Discovery Service.

Produces: `jobs.discovered` (JobDiscoveredEvent, payload NormalizedJob,
source_type != MANUAL_URL), one per discovered posting — see
docs/architecture/event-contracts.md#jobdiscoveredevent and
docs/architecture/kafka-topics.md#jobsdiscovered.
"""

from infrastructure.kafka.producer import EventProducer
from infrastructure.kafka.topics import Topic
from shared.events.envelope import EventEnvelope
from shared.types.dto import NormalizedJob
from shared.types.ids import CorrelationId

PRODUCER_NAME = "job-discovery-service"
SOURCE_TAG = "automatic"


def publish_job_discovered(
    payload: NormalizedJob,
    *,
    producer: EventProducer,
    correlation_id: CorrelationId | None = None,
) -> EventEnvelope[NormalizedJob]:
    """Publish `payload` on `jobs.discovered`, tagged `source=automatic`.

    Each discovered posting starts its own new causal chain, same as the
    manual path — there is no upstream event a discovery run responds to.
    """
    return producer.publish(
        Topic.JOBS_DISCOVERED,
        payload,
        correlation_id=correlation_id,
        source=SOURCE_TAG,
    )


__all__ = ["PRODUCER_NAME", "SOURCE_TAG", "publish_job_discovered"]
