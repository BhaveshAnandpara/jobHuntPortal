"""Port for "this opportunity can't proceed until the user fixes something".

The problem this solves
-----------------------
Job Matching, Contact Discovery and Outreach can each discover, mid-pipeline,
that the user this work belongs to has no usable AI provider configuration
(`infrastructure.llm.LlmNotConfiguredError`). That is not a pipeline fault
and not worth a retry: every retry and every dead-letter of that message
fails identically until the user goes into Settings and fixes their key. The
right outcome is to pause *that one opportunity* visibly and let the consumer
loop carry on for everyone else (docs/llm-provider-config/TECH.md Task 8).

"Pause it visibly" means writing `Application.status`/`notes` and an
`application_history` row — and `Application` is owned by Tracking Service
alone (`shared/types/domain/application.py`: "Modifiable by Tracking Service
only"). A component may not import `tracking`, call its `/applications` API,
or write its tables; those rules are enforced, not merely documented, by
`tests/integration/test_architecture_audit.py` and
`tests/integration/test_tracking_lifecycle.py`. A new Kafka topic is not the
answer either — `infrastructure/kafka/topics.py` is a locked ten-topic
contract ("Adding a topic is an architecture change ... never a local
decision").

The resolution: dependency inversion
------------------------------------
The three reporting components depend on this *abstraction*, which lives in
`shared/` and knows nothing about Tracking Service. Tracking Service supplies
the implementation. The composition root that runs the consumers
(`scripts/run_consumers.py`, which already imports every component) wires the
two together at startup via `set_opportunity_blocked_reporter`.

So the source-level dependency arrow runs *away* from Tracking
(`matching -> shared <- tracking`), never toward it, and
dependency-graph.md's "No circular dependencies" rule holds unchanged: no
component gains a compile-time or runtime edge to Tracking Service, and
Tracking still only ever writes its own tables.

Unregistered is a supported state, not a bug: a process that consumes events
without a Tracking Service (a component-level test, a partial deployment)
still gets the warning log, and the report is simply not persisted. Reporting
is therefore always safe to call and never raises — see
`report_opportunity_blocked`.
"""

from __future__ import annotations

from collections.abc import Awaitable
from enum import Enum
from typing import Protocol

from infrastructure.logging import format_context, get_logger
from shared.types.enums import EventType
from shared.types.ids import CorrelationId, JobId, UserId

logger = get_logger(__name__)


class OpportunityBlockedReason(str, Enum):
    """Why an opportunity was paused.

    Deliberately a small, closed vocabulary rather than free text: the
    implementing side maps each member onto its own lifecycle state, so a new
    member is a decision both sides make together. It is *not* an
    `ErrorCode` — nothing here failed; the pipeline is waiting on the user.
    """

    LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"
    """The user has no validated AI provider configuration on file, so no AI
    step can run for them. Recoverable entirely by the user, in Settings."""


class OpportunityBlockedReporter(Protocol):
    """What an implementation of this port must accept.

    A `typing.Protocol`, matching `shared/contracts/repository.py`'s
    precedent: the implementation satisfies it structurally, with no
    inheritance and no import in either direction.
    """

    async def __call__(
        self,
        *,
        job_id: JobId,
        user_id: UserId,
        reason: OpportunityBlockedReason,
        reported_by: str,
        correlation_id: CorrelationId | None = None,
        source_event_type: EventType | None = None,
        company: str | None = None,
        title: str | None = None,
    ) -> Awaitable[None] | None: ...


_reporter: OpportunityBlockedReporter | None = None


def set_opportunity_blocked_reporter(reporter: OpportunityBlockedReporter | None) -> None:
    """Register the implementation (or `None` to unregister).

    Called by the composition root that assembles a consuming process —
    `scripts/run_consumers.py` — and by tests that want the persistence side
    exercised. Mirrors every other `set_*` DI seam in this codebase
    (`set_event_producer`, `set_session_factory`, `set_graph`).
    """
    global _reporter
    _reporter = reporter


def get_opportunity_blocked_reporter() -> OpportunityBlockedReporter | None:
    """The currently-registered implementation, if any."""
    return _reporter


async def report_opportunity_blocked(
    *,
    job_id: JobId,
    user_id: UserId,
    reason: OpportunityBlockedReason,
    reported_by: str,
    correlation_id: CorrelationId | None = None,
    source_event_type: EventType | None = None,
    company: str | None = None,
    title: str | None = None,
) -> None:
    """Report that one opportunity is paused, and never raise.

    Always logs at `warning` with `user_id` and `correlation_id` — that log
    line is the part that is guaranteed regardless of what is registered.
    Then delegates to the registered reporter, if one is.

    **This function swallows every exception the reporter raises**, on
    purpose. Its callers are Kafka message handlers that have just chosen
    *not* to fail the message; letting a bookkeeping failure resurrect an
    exception would undo exactly that choice and send a message that can
    never succeed around the retry/DLQ loop anyway. A failure to record is
    logged with a traceback and otherwise ignored.
    """
    context = format_context(
        job_id=job_id,
        user_id=user_id,
        correlation_id=correlation_id,
        reason=reason.value,
        reported_by=reported_by,
    )
    logger.warning("Opportunity blocked | %s", context)

    reporter = _reporter
    if reporter is None:
        logger.warning(
            "No opportunity-blocked reporter registered; the block was logged "
            "but not recorded on the opportunity | %s",
            context,
        )
        return

    try:
        result = reporter(
            job_id=job_id,
            user_id=user_id,
            reason=reason,
            reported_by=reported_by,
            correlation_id=correlation_id,
            source_event_type=source_event_type,
            company=company,
            title=title,
        )
        if result is not None:
            await result
    except Exception:
        # Broad on purpose — see the docstring: reporting must never raise.
        logger.exception("Failed to record a blocked opportunity | %s", context)


__all__ = [
    "OpportunityBlockedReason",
    "OpportunityBlockedReporter",
    "get_opportunity_blocked_reporter",
    "report_opportunity_blocked",
    "set_opportunity_blocked_reporter",
]
