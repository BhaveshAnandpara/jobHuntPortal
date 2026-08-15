"""JWT signing configuration, read from the environment.

Unlike `infrastructure/database/config.py`'s `DATABASE_URL` (a real,
usable local default), `JWT_SECRET_KEY` is a genuine secret — silently
defaulting it would mean every uninitialized deployment trusts the same
publicly-known key. `get_auth_settings()` raises a clear error instead of
guessing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_ALGORITHM = "HS256"
DEFAULT_EXPIRY_MINUTES = 10080  # 7 days — stateless tokens, no refresh flow
# in this pass, so this is the entire window before a re-login is required.


@dataclass(frozen=True)
class AuthSettings:
    """Resolved JWT settings for the running process."""

    secret_key: str
    algorithm: str = DEFAULT_ALGORITHM
    expiry_minutes: int = DEFAULT_EXPIRY_MINUTES


def get_auth_settings() -> AuthSettings:
    """Read the current process environment into an `AuthSettings`.

    Raises `RuntimeError` if `JWT_SECRET_KEY` is unset — this must be a
    deliberate, deployment-specific value, never an implicit default.
    """
    secret_key = os.environ.get("JWT_SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "JWT_SECRET_KEY is not set. Generate one (e.g. `python -c "
            "\"import secrets; print(secrets.token_urlsafe(32))\"`) and set "
            "it in .env."
        )

    algorithm = os.environ.get("JWT_ALGORITHM") or DEFAULT_ALGORITHM

    raw_expiry = os.environ.get("JWT_EXPIRY_MINUTES")
    expiry_minutes = int(raw_expiry) if raw_expiry else DEFAULT_EXPIRY_MINUTES

    return AuthSettings(
        secret_key=secret_key,
        algorithm=algorithm,
        expiry_minutes=expiry_minutes,
    )


__all__ = ["DEFAULT_ALGORITHM", "DEFAULT_EXPIRY_MINUTES", "AuthSettings", "get_auth_settings"]
