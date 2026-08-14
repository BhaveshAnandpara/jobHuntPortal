"""ApplicationHistory — append-only audit trail of every status transition
an Application has gone through. See
docs/architecture/domain-model.md#applicationhistory.

Ownership: Tracking Service. Modifiable by Tracking Service only
(insert-only; rows are never updated or deleted).
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.enums import ApplicationStatus, EventType
from shared.types.ids import ApplicationHistoryId, ApplicationId, CorrelationId


class ApplicationHistory(BaseModel):
    id: ApplicationHistoryId
    application_id: ApplicationId
    from_status: ApplicationStatus | None = None  # null for the first entry
    to_status: ApplicationStatus
    changed_at: datetime
    triggered_by: str  # source component or "user"
    # null if triggered by a direct API call
    source_event_type: EventType | None = None
    correlation_id: CorrelationId | None = None
