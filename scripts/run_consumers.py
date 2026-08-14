"""Real (non-demo) Kafka consumer workers for local development on Windows.

Not part of the application itself — never imported by src/ or tests/.

Why this exists: `scripts/run_server.py` only runs the HTTP API, which
*produces* Kafka events (job ingestion, outreach approval, ...) but never
consumes any. Nothing downstream of ingestion — matching, contact discovery,
outreach generation/send, tracking's `Application` rows — ever runs unless
something is actually polling each topic. The demo/E2E harness
(`e2e/backend_server.py`) fakes this by draining its in-memory broker
synchronously after each request; the real Kafka broker has no such drain,
so this process is the real equivalent: one `EventConsumer` per
(topic, consumer group) pair documented in docs/architecture/kafka-topics.md,
each polling in its own thread.

`EventConsumer.run()` is a plain blocking loop (`confluent_kafka.Consumer
.poll()` is a blocking C call, not asyncio), so threads are enough — no
event loop is shared between them. What *does* need care: every `handle_*`
function drives its real async logic via a fresh `asyncio.run(...)` per
message (see e.g. `matching/consumers.py`'s `handle_job_discovered` —
`EventConsumer._process_message` calls `self.handler(envelope)` directly
without awaiting, so the handler must be sync and drive its own loop).
`psycopg`'s async Postgres driver cannot run on Windows' default
`ProactorEventLoop` (see `scripts/run_server.py`'s docstring for the full
story) — but unlike `uvicorn.Server.run()`, `asyncio.run()` *does* respect
the process-wide event loop policy, so setting
`WindowsSelectorEventLoopPolicy` once here, before any consumer thread
starts, is sufficient. `scripts/bootstrap_db.py` already proves this exact
fix works for its own `asyncio.run()` call.

No consumer here needs explicit LLM/HTTP/DB wiring beyond `.env`: every
workflow node and repository module already resolves a real default
(`LLMClient()` -> Ollama, `ProfileServiceClient()` -> a real HTTP call to
this same API process, DB session factory -> real Postgres) unless a
test/demo harness overrides it via a `set_*_client`/`set_session_factory`
seam — see e.g. `workflows/langgraph/job_matching/nodes.py`. The one
exception: `PeopleSearchClient` defaults to a `StaticPeopleSearchProvider([])`
(zero hits) since no third-party people-search credentials are configured
locally — Contact Discovery Service runs correctly but never finds a real
contact until a real provider is wired in.

Usage (from the repo root, real Postgres/Kafka/Ollama and
`scripts/run_server.py` already running):

    python scripts/run_consumers.py
"""

from __future__ import annotations

import asyncio
import sys
import threading

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv

load_dotenv()

import contacts.consumers as contacts_consumers
import matching.consumers as matching_consumers
import outreach.consumers as outreach_consumers
import tracking.consumers as tracking_consumers
from infrastructure.database.metadata import load_metadata
from infrastructure.kafka.consumer import EventConsumer
from infrastructure.kafka.topics import Topic
from infrastructure.logging import configure_logging, get_logger

load_metadata()
configure_logging()
logger = get_logger(__name__)

# One EventConsumer per (topic, consumer group) pair from
# docs/architecture/kafka-topics.md's per-topic tables. `profiles.updated`
# is omitted: matching/consumers.py's handle_profile_updated is
# intentionally deferred (re-matching-on-profile-update isn't implemented
# yet) and `applications.updated` has no consumer required today.
#
# Tracking Service consumes seven different topics, but each gets its own
# distinct group_id below (`tracking-service-<topic>`) rather than sharing
# one literal "tracking-service" group_id across all seven. Kafka group
# membership assumes members of one group_id converge on one subscription;
# seven `Consumer` clients joining the same group_id with seven different
# single-topic subscriptions never converges — confirmed directly against
# this project's real broker via `kafka-consumer-groups.sh --describe
# --group tracking-service`, which reported the group permanently stuck in
# "rebalancing" and never dispatched a single message. Each topic getting
# its own group_id (still logically "Tracking Service", just one Kafka
# consumer-group identity per topic it independently tracks offsets for)
# is the standard fix and matches how Job Matching's dedicated
# "job-matching-service" and Outreach's dedicated
# "outreach-service"/"outreach-service-send-worker" groups already work —
# each of those is also one group_id per one topic subscription.
_CONSUMERS: list[EventConsumer] = [
    EventConsumer(Topic.JOBS_DISCOVERED, "job-matching-service", matching_consumers.handle_job_discovered),
    EventConsumer(Topic.JOBS_DISCOVERED, "tracking-service-jobs-discovered", tracking_consumers.handle_job_discovered),
    EventConsumer(Topic.JOBS_MATCHED, "tracking-service-jobs-matched", tracking_consumers.handle_job_matched),
    EventConsumer(Topic.JOBS_SHORTLISTED, "tracking-service-jobs-shortlisted", tracking_consumers.handle_job_shortlisted),
    EventConsumer(Topic.CONTACTS_REQUESTED, "contact-discovery-service", contacts_consumers.handle_contacts_requested),
    EventConsumer(Topic.CONTACTS_FOUND, "outreach-service", outreach_consumers.handle_contacts_found),
    EventConsumer(Topic.CONTACTS_FOUND, "tracking-service-contacts-found", tracking_consumers.handle_contacts_found),
    EventConsumer(Topic.OUTREACH_GENERATED, "tracking-service-outreach-generated", tracking_consumers.handle_outreach_generated),
    EventConsumer(Topic.OUTREACH_APPROVED, "outreach-service-send-worker", outreach_consumers.handle_outreach_approved),
    EventConsumer(Topic.OUTREACH_APPROVED, "tracking-service-outreach-approved", tracking_consumers.handle_outreach_approved),
    EventConsumer(Topic.OUTREACH_SENT, "tracking-service-outreach-sent", tracking_consumers.handle_outreach_sent),
]


def _run(consumer: EventConsumer) -> None:
    logger.info("Consumer starting | topic=%s group_id=%s", consumer.topic.value, consumer.group_id)
    try:
        consumer.run()
    except Exception:
        logger.exception(
            "Consumer crashed | topic=%s group_id=%s", consumer.topic.value, consumer.group_id
        )


def main() -> None:
    threads = [
        threading.Thread(
            target=_run, args=(consumer,), daemon=True, name=f"{consumer.group_id}:{consumer.topic.value}"
        )
        for consumer in _CONSUMERS
    ]
    for thread in threads:
        thread.start()
    logger.info("All %d Kafka consumers started", len(threads))
    for thread in threads:
        thread.join()


if __name__ == "__main__":
    main()
