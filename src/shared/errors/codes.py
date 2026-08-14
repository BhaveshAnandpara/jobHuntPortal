"""Shared error codes.

Locked contract — see docs/architecture/shared-types.md#shared-error-codes.
Used in API error responses, WorkflowError.error_code, and DLQ metadata.
Components may not define new error codes ad hoc for concepts already
covered here; a genuinely new failure mode is added here first.
"""

from enum import Enum


class ErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    RESUME_PARSE_FAILED = "RESUME_PARSE_FAILED"
    INVALID_JOB_URL = "INVALID_JOB_URL"
    JOB_FETCH_FAILED = "JOB_FETCH_FAILED"
    NO_PROFILES_AVAILABLE = "NO_PROFILES_AVAILABLE"
    MATCHING_FAILED = "MATCHING_FAILED"
    CONTACT_SEARCH_FAILED = "CONTACT_SEARCH_FAILED"
    NO_CONTACTS_FOUND = "NO_CONTACTS_FOUND"
    OUTREACH_GENERATION_FAILED = "OUTREACH_GENERATION_FAILED"
    LLM_PROVIDER_ERROR = "LLM_PROVIDER_ERROR"
    EXTERNAL_SEND_FAILED = "EXTERNAL_SEND_FAILED"


__all__ = ["ErrorCode"]
