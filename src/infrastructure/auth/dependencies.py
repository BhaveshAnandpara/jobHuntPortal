"""The `get_current_user_id` FastAPI dependency — every protected route
across every service depends on this instead of accepting a client-supplied
`user_id`.

Not raw ASGI middleware: this codebase has none, and every component
already wires cross-cutting concerns (DB sessions, service instances) via
`Annotated[Type, Depends(...)]`. A dependency applied broadly behaves the
same way (blocks unauthenticated requests before a handler body runs)
while staying consistent with that convention and FastAPI's own exception
handling.

Uses `HTTPBearer(auto_error=False)` rather than the default `auto_error`
behavior — FastAPI's default raises a bare `{"detail": "Not authenticated"}`
on a missing header, which doesn't match this codebase's universal
`{"detail": {"code", "message"}}` error shape (see e.g.
`jobs/ingestion/api.py`'s `_http_error`). This dependency raises that shape
explicitly instead, for every failure mode (missing header, malformed
token, expired token) — one normalized 401, nothing route-specific.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from infrastructure.auth.errors import AuthError
from infrastructure.auth.jwt import decode_access_token
from shared.types.ids import UserId

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> UserId:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "missing bearer token"},
        )
    try:
        return decode_access_token(credentials.credentials)
    except AuthError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": error.code.value, "message": error.message},
        ) from error


CurrentUserIdDependency = Annotated[UserId, Depends(get_current_user_id)]


__all__ = ["CurrentUserIdDependency", "get_current_user_id"]
