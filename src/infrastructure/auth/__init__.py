"""Authentication infrastructure — password hashing and JWT issuance/
verification, shared by every component that needs to know "who is making
this call." Mirrors `infrastructure/database`, `infrastructure/kafka`,
`infrastructure/llm`: an internal Python interface, not an HTTP API.

Typical usage from a route file::

    from infrastructure.auth import CurrentUserIdDependency

    @router.post("/jobs/ingest-url")
    async def ingest_job_url_route(
        request: IngestJobUrlRequest,
        user_id: CurrentUserIdDependency,
        ...,
    ) -> JobResponse:
        ...

User Service (the only component that issues tokens) additionally uses
`hash_password`/`verify_password`/`create_access_token` directly — see
`users/service.py`.
"""

from infrastructure.auth.config import AuthSettings, get_auth_settings
from infrastructure.auth.dependencies import (
    CurrentUserIdDependency,
    get_current_user_id,
)
from infrastructure.auth.errors import AuthError
from infrastructure.auth.jwt import create_access_token, decode_access_token
from infrastructure.auth.passwords import hash_password, verify_password

__all__ = [
    "AuthError",
    "AuthSettings",
    "CurrentUserIdDependency",
    "create_access_token",
    "decode_access_token",
    "get_auth_settings",
    "get_current_user_id",
    "hash_password",
    "verify_password",
]
