"""Tests that every normalized error type in infrastructure.external.errors
carries what callers need: a shared ErrorCode, a message, provider info,
retryability, and an optional cause — and that no provider-specific
exception type is required to construct them.
"""

from infrastructure.external.errors import (
    ExternalIntegrationError,
    InvalidUrlError,
    JobSearchRequestError,
    MessageSendError,
    PageFetchError,
    PeopleSearchRequestError,
)
from shared.errors.codes import ErrorCode


def test_base_error_defaults():
    err = ExternalIntegrationError("boom")
    assert err.message == "boom"
    assert str(err) == "boom"
    assert err.provider is None
    assert err.retryable is True
    assert err.cause is None
    assert err.error_code == ErrorCode.JOB_FETCH_FAILED


def test_base_error_carries_provider_and_cause():
    cause = RuntimeError("root cause")
    err = ExternalIntegrationError(
        "boom", provider="acme", retryable=False, cause=cause
    )
    assert err.provider == "acme"
    assert err.retryable is False
    assert err.cause is cause


def test_page_fetch_error_maps_to_job_fetch_failed():
    err = PageFetchError("could not fetch")
    assert err.error_code == ErrorCode.JOB_FETCH_FAILED
    assert isinstance(err, ExternalIntegrationError)


def test_invalid_url_error_maps_to_invalid_job_url_and_is_never_retryable():
    err = InvalidUrlError("bad url", provider="playwright")
    assert err.error_code == ErrorCode.INVALID_JOB_URL
    assert err.retryable is False
    assert err.provider == "playwright"
    assert isinstance(err, PageFetchError)


def test_invalid_url_error_does_not_accept_retryable_override():
    # InvalidUrlError's constructor deliberately omits `retryable` from its
    # signature so a caller can never accidentally make an unfetchable URL
    # retryable.
    err = InvalidUrlError("bad url")
    assert err.retryable is False


def test_job_search_request_error_maps_to_job_fetch_failed():
    err = JobSearchRequestError("search failed")
    assert err.error_code == ErrorCode.JOB_FETCH_FAILED


def test_people_search_request_error_maps_to_contact_search_failed():
    err = PeopleSearchRequestError("search failed")
    assert err.error_code == ErrorCode.CONTACT_SEARCH_FAILED


def test_message_send_error_maps_to_external_send_failed():
    err = MessageSendError("send failed")
    assert err.error_code == ErrorCode.EXTERNAL_SEND_FAILED


def test_all_normalized_errors_are_external_integration_errors():
    for error_cls in (
        PageFetchError,
        InvalidUrlError,
        JobSearchRequestError,
        PeopleSearchRequestError,
        MessageSendError,
    ):
        assert issubclass(error_cls, ExternalIntegrationError)


def test_error_code_is_a_shared_error_code_member():
    for error_cls in (
        ExternalIntegrationError,
        PageFetchError,
        InvalidUrlError,
        JobSearchRequestError,
        PeopleSearchRequestError,
        MessageSendError,
    ):
        assert isinstance(error_cls.error_code, ErrorCode)
