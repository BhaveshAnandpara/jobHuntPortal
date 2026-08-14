"""JobSource — configuration for an automatic discovery source (a job
board, search query, or company careers feed). See
docs/architecture/domain-model.md#jobsource.

Ownership: Job Discovery Service. Modifiable by Job Discovery Service only.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from shared.types.enums import JobSourceType
from shared.types.ids import JobSourceId, UserId


class JobSource(BaseModel):
    id: JobSourceId
    user_id: UserId  # sources are configured per user
    name: str  # e.g. "LinkedIn — Backend roles"
    type: JobSourceType
    query_config: dict[str, Any] | None = None  # source-specific params (JSON)
    enabled: bool
    last_run_at: datetime | None = None
