"""Per-workflow-run context that doesn't fit `ContactDiscoveryState`'s
locked shape (docs/architecture/langgraph-state.md#contactdiscoverystate —
this component's task brief: "copy verbatim, it's a locked TypedDict").

Two gaps, resolved with the same technique
`workflows/langgraph/job_matching/context.py`'s docstring establishes for
its own `correlation_id` gap (a `contextvars.ContextVar`, asyncio-task-local,
set once by the consumer around `graph.ainvoke(...)` and read/written only
inside this workflow's own nodes — safe because a single LangGraph run
processes one unit of work end to end, in-process, per
langgraph-state.md's opening paragraph):

1. `correlation_id`: `persist_and_publish` must propagate the inbound
   `contacts.requested` envelope's `correlation_id` onto the published
   `ContactsFoundEvent` (event-contracts.md's propagation rule), but the
   locked state has no such field.

2. Per-contact persistence detail: `ContactDiscoveryState.ranked_contacts`
   is typed `list[RankedContact]` — the wire/event shape only
   (`contact_id`, `full_name`, `headline`, `contact_type`, `profile_url`,
   `relevance_score`). `persist_and_publish` also needs each contact's
   `email` (present on `Contact`, never on `RankedContact`) and its
   `ContactScore` breakdown (`same_company`, `department_relevance`,
   `role_similarity`, `seniority_fit`) to write `contact_rankings`.
   database-ownership.md#contact_rankings is explicit that this breakdown
   "is internal detail (score breakdown never leaves via the event
   payload — only relevance_score does)", so it was never going to become
   a `ContactDiscoveryState` field either — the locked state simply has no
   slot for it. `rank_contacts` computes this detail once per contact
   (keyed by the `contact_id` it mints for the `RankedContact`) and
   `persist_and_publish` reads it back.

Both are documented, minimal fills for a real gap in the locked contract,
not a silent divergence from it — see this component's final implementation
report for the same framing used for job_matching's precedent.
"""

from __future__ import annotations

import contextvars

from pydantic import BaseModel

from shared.types.ids import ContactId, CorrelationId

_correlation_id: contextvars.ContextVar[CorrelationId | None] = contextvars.ContextVar(
    "contact_discovery_correlation_id", default=None
)


def set_correlation_id(correlation_id: CorrelationId | None) -> contextvars.Token:
    return _correlation_id.set(correlation_id)


def get_correlation_id() -> CorrelationId | None:
    return _correlation_id.get()


def reset_correlation_id(token: contextvars.Token) -> None:
    _correlation_id.reset(token)


class ContactPersistenceDetail(BaseModel):
    """Everything `persist_and_publish` needs to build `Contact`/
    `ContactScore` rows that `RankedContact` alone doesn't carry."""

    company: str
    email: str | None
    same_company: bool
    role_similarity: float
    department_relevance: float | None
    seniority_fit: float | None


_contact_details: contextvars.ContextVar[dict[ContactId, ContactPersistenceDetail] | None] = (
    contextvars.ContextVar("contact_discovery_contact_details", default=None)
)


def set_contact_details(
    details: dict[ContactId, ContactPersistenceDetail],
) -> contextvars.Token:
    return _contact_details.set(details)


def get_contact_details() -> dict[ContactId, ContactPersistenceDetail]:
    """Returns the current run's mutable detail dict (never `None` —
    `rank_contacts` writes into the same dict object `set_contact_details`
    installed, so callers may safely mutate the returned dict in place).

    Deliberately `is None` rather than falsy-checked (`... or {}`): an
    empty dict is falsy in Python, and `set_contact_details({})` is exactly
    what every real run starts with, so a truthiness check would silently
    hand back a disconnected new dict instead of the shared one the moment
    it's empty — breaking `rank_contacts` -> `persist_and_publish` handoff
    for the (extremely common) very next call.
    """
    details = _contact_details.get()
    return details if details is not None else {}


def reset_contact_details(token: contextvars.Token) -> None:
    _contact_details.reset(token)


__all__ = [
    "ContactPersistenceDetail",
    "get_contact_details",
    "get_correlation_id",
    "reset_contact_details",
    "reset_correlation_id",
    "set_contact_details",
    "set_correlation_id",
]
