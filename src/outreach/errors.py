"""Local error type for Outreach Service.

`OutreachError` is raised for the workflow's `OUTREACH_GENERATION_FAILED`
condition (docs/architecture/langgraph-state.md#outreachgenerationstate's
`generate_message` node: "aborts — no draft is persisted or published on
failure, since a human cannot approve a message that doesn't exist") and
for any otherwise-unclassified failure the top-level `contacts.found` /
`outreach.approved` consumers catch
(docs/architecture/component-contracts.md#outreach-service).

Same normalization contract as `matching.errors.MatchingError` /
`contacts.errors.ContactDiscoveryError`: it must still be *raised* (not
swallowed), because `infrastructure.kafka.consumer.EventConsumer` only
retries-then-DLQs a message when the registered handler raises, and
`EventConsumer._classify_error` reads this exception's duck-typed
`error_code` attribute to populate `EventMetadata.error_code` on DLQ
republish.

`error_code` defaults to `OUTREACH_GENERATION_FAILED` but callers may
construct with a different code (e.g. `LLM_PROVIDER_ERROR`,
`EXTERNAL_SEND_FAILED`) when the underlying cause is already known and
classifiable — same default/override split as `MatchingError`.
"""

from __future__ import annotations

from shared.errors.codes import ErrorCode


class OutreachError(Exception):
    """Raised for a failure inside the Outreach Generation workflow or
    either of this component's two Kafka consumers (`contacts.found`,
    `outreach.approved` send-worker) that must reach `EventConsumer`'s
    retry/DLQ mechanism.
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: ErrorCode = ErrorCode.OUTREACH_GENERATION_FAILED,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code


__all__ = ["OutreachError"]
