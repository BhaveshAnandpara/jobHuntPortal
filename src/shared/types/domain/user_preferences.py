"""UserPreferences — search behavior configuration used by Job Discovery
and as a matching input signal (location/comp/remote fit). See
docs/architecture/domain-model.md#userpreferences.

Ownership: User Service. Modifiable by User Service only.
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.enums import RemoteWorkPreference
from shared.types.ids import UserId, UserPreferencesId


class UserPreferences(BaseModel):
    id: UserPreferencesId
    user_id: UserId  # one active row per user
    target_roles: list[str] = []
    target_locations: list[str] = []
    remote_preference: RemoteWorkPreference | None = None
    excluded_companies: list[str] = []
    min_salary: int | None = None  # in user's stated currency
    salary_currency: str | None = None  # ISO 4217 code
    updated_at: datetime
