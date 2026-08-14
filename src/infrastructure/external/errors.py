"""Normalized external-integration errors.

Every adapter in this package raises one of these, and every one carries a
code from the shared `ErrorCode` vocabulary
(docs/architecture/shared-types.md#shared-error-codes). Provider-specific
exception types never escape this package — callers match on `ErrorCode`,
not on a third-party library's exception class.
"""

from shared.errors.codes import ErrorCode


class ExternalIntegrationError(Exception):
    """Base for every failure raised by the External Integrations Layer."""

    error_code: ErrorCode = ErrorCode.JOB_FETCH_FAILED

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        retryable: bool = True,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.retryable = retryable
        self.cause = cause


class PageFetchError(ExternalIntegrationError):
    """Network/browser/parse failure while fetching a page."""

    error_code = ErrorCode.JOB_FETCH_FAILED


class InvalidUrlError(PageFetchError):
    """The submitted URL is not fetchable at all — retrying cannot help."""

    error_code = ErrorCode.INVALID_JOB_URL

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, provider=provider, retryable=False, cause=cause)


class JobSearchRequestError(ExternalIntegrationError):
    """A job-board search provider call failed."""

    error_code = ErrorCode.JOB_FETCH_FAILED


class PeopleSearchRequestError(ExternalIntegrationError):
    """A people-search provider call failed."""

    error_code = ErrorCode.CONTACT_SEARCH_FAILED


class MessageSendError(ExternalIntegrationError):
    """An email/LinkedIn send provider call failed."""

    error_code = ErrorCode.EXTERNAL_SEND_FAILED


__all__ = [
    "ExternalIntegrationError",
    "InvalidUrlError",
    "JobSearchRequestError",
    "MessageSendError",
    "PageFetchError",
    "PeopleSearchRequestError",
]
