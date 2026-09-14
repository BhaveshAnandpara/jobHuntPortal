"""Shared Protocol/ABC definitions — cross-cutting interface boundaries
that every component's own internal modules implement. See
docs/architecture/repository-structure.md.

Nothing in this package encodes business logic or entity-specific fields;
that belongs in each component's own `repository.py`/`service.py`.
"""

from shared.contracts.opportunity_blocked import (
    OpportunityBlockedReason,
    OpportunityBlockedReporter,
    get_opportunity_blocked_reporter,
    report_opportunity_blocked,
    set_opportunity_blocked_reporter,
)
from shared.contracts.repository import Repository

__all__ = [
    "OpportunityBlockedReason",
    "OpportunityBlockedReporter",
    "Repository",
    "get_opportunity_blocked_reporter",
    "report_opportunity_blocked",
    "set_opportunity_blocked_reporter",
]
