"""Local error type for Job Matching Service.

`MatchingError` is raised for the workflow's `MATCHING_FAILED` condition
(docs/architecture/langgraph-state.md#jobmatchingstate's `persist_and_publish`
node: "DB or publish failure — the whole WorkflowExecution is marked FAILED,
no partial event is published") and for any otherwise-unclassified failure
the top-level `jobs.discovered` consumer catches
(docs/architecture/component-contracts.md#job-matching-service).

It must still be *raised* (not swallowed) by both the LangGraph node and the
consumer, because `infrastructure.kafka.consumer.EventConsumer` only
retries-then-DLQs a message when the registered handler raises
(docs/architecture/kafka-topics.md's retry strategy). "Never let a raw
exception escape" (this component's task brief) means every failure must be
*normalized* to carry a duck-typed `error_code` attribute before it leaves
this component's boundary — `infrastructure.kafka.consumer._classify_error`
reads exactly that attribute to populate `EventMetadata.error_code` on DLQ
republish — not that failures stop propagating.
"""

from __future__ import annotations

from shared.errors.codes import ErrorCode


class MatchingError(Exception):
    """Raised for a `MATCHING_FAILED` (or otherwise unclassified) failure
    inside the Job Matching workflow or its Kafka consumer.

    `error_code` defaults to `MATCHING_FAILED` but callers may construct
    with a different code (e.g. `LLM_PROVIDER_ERROR`) when the underlying
    cause is already known and classifiable.
    """

    def __init__(self, message: str, *, error_code: ErrorCode = ErrorCode.MATCHING_FAILED) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code


__all__ = ["MatchingError"]
