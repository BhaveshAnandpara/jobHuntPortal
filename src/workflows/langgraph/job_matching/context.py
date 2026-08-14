"""Per-workflow-run context that doesn't fit `JobMatchingState`'s locked
shape.

`JobMatchingState` (state.py, locked per langgraph-state.md — "do not
modify it") has no `correlation_id` field, yet `persist_and_publish` must
propagate the inbound `jobs.discovered` envelope's `correlation_id` onto the
`JobMatchedEvent`/`JobShortlistedEvent` it publishes
(event-contracts.md's correlation_id propagation rule;
shared-types.md#identifiers: "generated once at the origin of a chain ...
propagated unchanged through every downstream event"). Rather than
smuggling it into a state field the documented contract doesn't have, it
travels via a `contextvars.ContextVar` set by the consumer immediately
around `graph.ainvoke(...)` and read only inside `persist_and_publish`.
`ContextVar` is asyncio-task-local, so this stays correct even if multiple
workflow runs are ever in flight concurrently in one process (today's Kafka
consumer processes one message at a time, but this doesn't rely on that).

This is a deliberate, documented fill for a real gap in the locked state
contract — see the Job Matching Service implementation report's Issues
section.
"""

from __future__ import annotations

import contextvars

from shared.types.ids import CorrelationId

_correlation_id: contextvars.ContextVar[CorrelationId | None] = contextvars.ContextVar(
    "job_matching_correlation_id", default=None
)


def set_correlation_id(correlation_id: CorrelationId | None) -> contextvars.Token:
    return _correlation_id.set(correlation_id)


def get_correlation_id() -> CorrelationId | None:
    return _correlation_id.get()


def reset_correlation_id(token: contextvars.Token) -> None:
    _correlation_id.reset(token)


__all__ = ["get_correlation_id", "reset_correlation_id", "set_correlation_id"]
