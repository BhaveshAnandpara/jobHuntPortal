"""JWT encode/decode — HS256, symmetric (single backend process, no need
for asymmetric keys).

Claims are deliberately minimal: `sub` (the user id, the only thing any
caller of `decode_access_token` needs), `iat`, and `exp`. Nothing else is
ever placed in the token — display fields like email/display_name are not
claims here, since a caller needing those already has `user_id` and can
ask the User Service for them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt as pyjwt

from infrastructure.auth.config import AuthSettings, get_auth_settings
from infrastructure.auth.errors import AuthError
from shared.errors.codes import ErrorCode
from shared.types.ids import UserId


def create_access_token(user_id: UserId, *, settings: AuthSettings | None = None) -> str:
    """Mint a signed token encoding `user_id`, valid for
    `settings.expiry_minutes` (or the env-resolved default)."""
    cfg = settings or get_auth_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=cfg.expiry_minutes),
    }
    return pyjwt.encode(payload, cfg.secret_key, algorithm=cfg.algorithm)


def decode_access_token(token: str, *, settings: AuthSettings | None = None) -> UserId:
    """Verify `token`'s signature and expiry, returning the `UserId` it
    encodes.

    Raises `AuthError(ErrorCode.UNAUTHORIZED, ...)` for any failure —
    expired, tampered, malformed, or missing/non-UUID `sub` claim. Callers
    never need to distinguish which; see this module's docstring.
    """
    cfg = settings or get_auth_settings()
    try:
        payload = pyjwt.decode(token, cfg.secret_key, algorithms=[cfg.algorithm])
    except pyjwt.ExpiredSignatureError as exc:
        raise AuthError(ErrorCode.UNAUTHORIZED, "token has expired") from exc
    except pyjwt.InvalidTokenError as exc:
        raise AuthError(ErrorCode.UNAUTHORIZED, "invalid token") from exc

    sub = payload.get("sub")
    try:
        return UserId(UUID(str(sub)))
    except (TypeError, ValueError) as exc:
        raise AuthError(ErrorCode.UNAUTHORIZED, "token subject is not a valid user id") from exc


__all__ = ["create_access_token", "decode_access_token"]
