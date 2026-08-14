"""Local error type for Contact Discovery Service.

`ContactDiscoveryError` is raised for the workflow's DB/publish failure
path (docs/architecture/langgraph-state.md#contactdiscoverystate's
`persist_and_publish` node: "none beyond standard DB/publish failure —
WorkflowExecution marked FAILED if so") and for any otherwise-unclassified
failure the top-level `contacts.requested` consumer catches
(docs/architecture/component-contracts.md#contact-discovery-service).

Same normalization contract as `matching.errors.MatchingError`: it must
still be *raised* (not swallowed), because
`infrastructure.kafka.consumer.EventConsumer` only retries-then-DLQs a
message when the registered handler raises, and
`EventConsumer._classify_error` reads this exception's duck-typed
`error_code` attribute to populate `EventMetadata.error_code` on DLQ
republish.

`error_code` defaults to `CONTACT_SEARCH_FAILED` — the shared `ErrorCode`
vocabulary has no generic "contact discovery workflow failed" member the
way Job Matching Service has `MATCHING_FAILED`
(shared-types.md#shared-error-codes lists only `CONTACT_SEARCH_FAILED`,
`NO_CONTACTS_FOUND`, and `LLM_PROVIDER_ERROR` for this component, and
`component-contracts.md`'s error list for the whole `contacts.requested`
consumer is exactly those three), so `CONTACT_SEARCH_FAILED` is used as
the closest-fit catch-all default for a whole-workflow-run failure,
overridden explicitly (e.g. `LLM_PROVIDER_ERROR`) whenever the underlying
cause is already known and classifiable — same pattern as
`MatchingError`'s own default/override split.
"""

from __future__ import annotations

from shared.errors.codes import ErrorCode


class ContactDiscoveryError(Exception):
    """Raised for a failure inside the Contact Discovery workflow or its
    Kafka consumer that must reach `EventConsumer`'s retry/DLQ mechanism.
    """

    def __init__(
        self, message: str, *, error_code: ErrorCode = ErrorCode.CONTACT_SEARCH_FAILED
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code


__all__ = ["ContactDiscoveryError"]
