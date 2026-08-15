"""Normalized authentication failures.

Carries a shared `ErrorCode` (docs/architecture/shared-types.md#shared-error-codes)
rather than defining new failure vocabulary, matching every other
component-local error type in this codebase (`UserError`,
`JobIngestionError`, ...). Every failure this layer raises — a wrong
password at login, or a missing/malformed/expired token on a protected
call — surfaces as `ErrorCode.UNAUTHORIZED`: both are the same class of
failure from the caller's point of view, and neither should reveal which
specific check failed.
"""

from shared.errors.codes import ErrorCode


class AuthError(Exception):
    """Raised by password/JWT verification and the `get_current_user_id`
    dependency."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


__all__ = ["AuthError"]
