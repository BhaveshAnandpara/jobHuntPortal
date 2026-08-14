"""User — the account holder. Represents one person using the platform,
independent of profession. See docs/architecture/domain-model.md#user.

Ownership: User Service. Modifiable by User Service only.
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.ids import UserId


class User(BaseModel):
    id: UserId
    email: str
    display_name: str
    created_at: datetime
    timezone: str | None = None  # IANA tz name, used for follow-up scheduling
