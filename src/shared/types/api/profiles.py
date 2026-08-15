"""Resume/Profile Service API request/response contracts.
See docs/architecture/api-contracts.md#resumeprofile-service.

Note: `GET /profiles` and `GET /profiles/{profile_id}` return
`shared.types.dto.ResumeProfile` directly — there is no separate response
wrapper for those endpoints, per api-contracts.md.
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.enums import ResumeStatus
from shared.types.ids import ResumeId, UserId


class CreateResumeRequest(BaseModel):
    file_name: str
    # Wire format is a base64 string inside the JSON body (per
    # docs/frontend/api-mapping.md's already-documented contract), not raw
    # bytes. Plain Pydantic `bytes` does NOT base64-decode a JSON string —
    # it UTF-8-encodes the string's own characters, silently corrupting
    # every uploaded resume. Kept as `str` (not `pydantic.Base64Bytes`) so
    # malformed base64 can be caught in `ProfileService.upload_resume()`
    # and mapped onto this service's normalized `{"code","message"}` error
    # shape via the existing `ProfileError`/`ErrorCode.VALIDATION_ERROR`
    # path, instead of surfacing FastAPI's default (non-normalized) 422.
    file_content: str


class ResumeResponse(BaseModel):
    id: ResumeId
    user_id: UserId
    file_name: str
    status: ResumeStatus
    uploaded_at: datetime


__all__ = ["CreateResumeRequest", "ResumeResponse"]
