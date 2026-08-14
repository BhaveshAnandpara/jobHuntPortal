"""Job Ingestion Service local error type.

Carries a shared `ErrorCode` (docs/architecture/shared-types.md#shared-error-codes)
rather than defining new failure vocabulary — see
docs/architecture/repository-structure.md#contract-naming-conventions
("`<Domain>Error` locally; shared codes via `ErrorCode`").
"""

from shared.errors.codes import ErrorCode


class JobIngestionError(Exception):
    """Raised when a manual URL cannot be turned into a normalized job."""

    def __init__(self, error_code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


__all__ = ["JobIngestionError"]
