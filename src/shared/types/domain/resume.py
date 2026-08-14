"""Resume — an uploaded resume file plus its raw extracted text. Distinct
from CandidateProfile, which is the *understood* profile derived from it.
See docs/architecture/domain-model.md#resume.

Ownership: Resume/Profile Service. Modifiable by Resume/Profile Service only.
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.enums import ResumeStatus
from shared.types.ids import ResumeId, UserId


class Resume(BaseModel):
    id: ResumeId
    user_id: UserId
    file_name: str
    storage_uri: str  # location of the original file
    raw_text: str | None = None  # extracted text; absent until parsed
    status: ResumeStatus
    uploaded_at: datetime
    parsed_at: datetime | None = None  # set when parsing completes
    parse_error: str | None = None  # set only if status == PARSE_FAILED
