"""Job Discovery Service local error type.

Carries a shared `ErrorCode` (docs/architecture/shared-types.md#shared-error-codes)
rather than defining new failure vocabulary — mirrors
`jobs.ingestion.errors.JobIngestionError`.
"""

from shared.errors.codes import ErrorCode


class JobDiscoveryError(Exception):
    """Raised for Job Discovery Service failures that must reach an API
    caller (e.g. `POST /job-sources` validation). Per-posting failures
    during an automatic discovery run are caught and logged, never raised
    to a caller — see `jobs.discovery.service.run_discovery`.
    """

    def __init__(self, error_code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


__all__ = ["JobDiscoveryError"]
