"""Per-workflow-run context that doesn't fit `OutreachGenerationState`'s
locked shape.

`OutreachGenerationState` (state.py, locked per langgraph-state.md — "do
not modify it") has no `job` field, only `job_id`/`user_id`. This
component's task brief is explicit about the intended fix: "note
OutreachGenerationState has no job field, only job_id/user_id, so plan to
carry whatever job context you need for personalization through your own
consumer's local variables into the LLM prompt construction, not through
the locked state shape." Same technique
`workflows/langgraph/job_matching/context.py`'s and
`workflows/langgraph/contact_discovery/context.py`'s docstrings establish
for their own locked-state gaps: a `contextvars.ContextVar`,
asyncio-task-local, set once by the consumer immediately around
`graph.ainvoke(...)` and read only inside this workflow's own nodes —
correct because a single LangGraph run processes one unit of work end to
end, in-process (langgraph-state.md's opening paragraph).

Two contextvars:

  - `correlation_id`: `persist_and_publish` must propagate the inbound
    `contacts.found` envelope's `correlation_id` onto the published
    `OutreachGeneratedEvent` (event-contracts.md's propagation rule) — the
    locked state has no such field either. Identical precedent to the two
    sibling workflows' own `correlation_id` contextvar.

  - `job_context`: the job's `company`/`title`, obtained by
    `outreach.consumers` via a third runtime API call, `GET /jobs/{job_id}`
    (Job Ingestion Service — dependency-graph.md's
    `Outreach Service ──► Job Ingestion Service` edge), alongside the
    `GET /jobs/{job_id}/matches` -> `GET /profiles/{profile_id}` chain used
    for `selected_resume_id`/`candidate_profile`. Neither `JobMatchResponse`
    nor `ContactRankingResult` carries `company`/`title` — those live only
    on `NormalizedJob`/the `jobs` table, which this component has no direct
    read access to per database-ownership.md; `GET /jobs/{job_id}` is the
    designated read path for that data (see `outreach.clients
    .JobIngestionClient`). `generate_message` reads it to build a
    personalized prompt. Only the two fields actually needed for
    personalization/formatting are carried — no full `NormalizedJob`
    duplication into workflow context.
"""

from __future__ import annotations

import contextvars

from pydantic import BaseModel

from shared.types.ids import CorrelationId

_correlation_id: contextvars.ContextVar[CorrelationId | None] = contextvars.ContextVar(
    "outreach_generation_correlation_id", default=None
)


def set_correlation_id(correlation_id: CorrelationId | None) -> contextvars.Token:
    return _correlation_id.set(correlation_id)


def get_correlation_id() -> CorrelationId | None:
    return _correlation_id.get()


def reset_correlation_id(token: contextvars.Token) -> None:
    _correlation_id.reset(token)


class JobContext(BaseModel):
    """Everything `generate_message` needs for personalization that
    `OutreachGenerationState` doesn't carry (job_id/user_id only)."""

    company: str
    title: str


_job_context: contextvars.ContextVar[JobContext | None] = contextvars.ContextVar(
    "outreach_generation_job_context", default=None
)


def set_job_context(context: JobContext) -> contextvars.Token:
    return _job_context.set(context)


def get_job_context() -> JobContext | None:
    return _job_context.get()


def reset_job_context(token: contextvars.Token) -> None:
    _job_context.reset(token)


__all__ = [
    "JobContext",
    "get_correlation_id",
    "get_job_context",
    "reset_correlation_id",
    "reset_job_context",
    "set_correlation_id",
    "set_job_context",
]
