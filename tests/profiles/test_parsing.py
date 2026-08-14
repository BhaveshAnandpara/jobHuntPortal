"""Unit tests for the Resume Parsing Workflow (profiles/parsing.py).

No live Ollama server required — every LLM interaction goes through a real
`infrastructure.llm.LLMClient` wired to `ScriptedLLMProvider`
(tests/profiles/conftest.py:make_llm_client). Covers success, failure, and
profession-independence (software engineer, mechanical engineer, HR resumes
all flow through the identical, unbranched extraction path).
"""

from __future__ import annotations

import pytest

from infrastructure.llm import LLMConfig
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from profiles.parsing import (
    ExtractedResumeProfile,
    ResumeParsingError,
    extract_profile_fields,
    extract_text,
    parse_resume,
)
from shared.errors.codes import ErrorCode
from shared.types.enums import ProfileStatus
from tests.profiles.conftest import (
    llm_response,
    llm_response_text,
    make_llm_client,
    make_resume,
)

# ---------------------------------------------------------------------------
# extract_text
# ---------------------------------------------------------------------------


def test_extract_text_txt_passthrough() -> None:
    text = extract_text("resume.txt", b"Experienced Mechanical Engineer.")
    assert text == "Experienced Mechanical Engineer."


def test_extract_text_pdf_best_effort_strips_control_bytes() -> None:
    content = b"Some \x00\x01 resume \x02 content"
    text = extract_text("resume.pdf", content)
    assert "resume" in text
    assert "content" in text
    assert "\x00" not in text


def test_extract_text_docx_best_effort() -> None:
    text = extract_text("resume.docx", "HR Business Partner résumé".encode())
    assert "HR Business Partner" in text


def test_extract_text_rejects_unsupported_extension() -> None:
    with pytest.raises(ResumeParsingError) as excinfo:
        extract_text("resume.exe", b"whatever")
    assert excinfo.value.code == ErrorCode.RESUME_PARSE_FAILED


def test_extract_text_rejects_empty_content() -> None:
    with pytest.raises(ResumeParsingError) as excinfo:
        extract_text("resume.txt", b"   \n\t  ")
    assert excinfo.value.code == ErrorCode.RESUME_PARSE_FAILED


# ---------------------------------------------------------------------------
# extract_profile_fields — profession independence
# ---------------------------------------------------------------------------

_SOFTWARE_ENGINEER_FIELDS = {
    "title": "Backend Software Engineer",
    "summary": "Builds distributed backend systems.",
    "skills": ["Python", "Kafka", "PostgreSQL"],
    "experience_years": 5.0,
    "seniority": "Senior",
    "education": [],
    "certifications": [],
    "projects": ["Rebuilt the payments service"],
    "industries": ["Software"],
    "target_roles": ["Backend Engineer"],
}

_MECHANICAL_ENGINEER_FIELDS = {
    "title": "Mechanical Design Engineer",
    "summary": "Designs precision mechanical assemblies.",
    "skills": ["SolidWorks", "GD&T", "FEA"],
    "experience_years": 8.0,
    "seniority": "Lead",
    "education": [],
    "certifications": ["PE License"],
    "projects": ["Redesigned a turbine housing"],
    "industries": ["Manufacturing"],
    "target_roles": ["Mechanical Design Engineer"],
}

_HR_FIELDS = {
    "title": "HR Business Partner",
    "summary": "Partners with leadership on talent strategy.",
    "skills": ["Talent Acquisition", "Employee Relations", "Workday"],
    "experience_years": 6.0,
    "seniority": "Manager",
    "education": [],
    "certifications": ["SHRM-CP"],
    "projects": ["Led a company-wide DEI initiative"],
    "industries": ["Human Resources"],
    "target_roles": ["HR Business Partner", "Talent Acquisition Lead"],
}


@pytest.mark.parametrize(
    "fields",
    [_SOFTWARE_ENGINEER_FIELDS, _MECHANICAL_ENGINEER_FIELDS, _HR_FIELDS],
    ids=["software-engineer", "mechanical-engineer", "hr"],
)
def test_extract_profile_fields_is_profession_independent(fields: dict) -> None:
    """The same extraction code path must faithfully carry through
    whatever the model returns for *any* profession — no branch in
    profiles/parsing.py may special-case software engineering (or any
    other single profession).
    """
    client, _ = make_llm_client([llm_response(fields)])
    result = extract_profile_fields("irrelevant raw text", client=client)

    assert isinstance(result, ExtractedResumeProfile)
    assert result.title == fields["title"]
    assert result.skills == fields["skills"]
    assert result.seniority == fields["seniority"]
    assert result.certifications == fields["certifications"]
    assert result.target_roles == fields["target_roles"]


def test_extraction_prompt_does_not_assume_a_single_profession() -> None:
    client, provider = make_llm_client([llm_response(_SOFTWARE_ENGINEER_FIELDS)])
    extract_profile_fields("irrelevant raw text", client=client)

    sent = provider.requests[0]
    assert "any profession" in sent.prompt.lower()
    assert "do not assume software engineering" in sent.prompt.lower()
    assert sent.system is not None
    assert "never assume the candidate is a software engineer" in sent.system.lower()


def test_extract_profile_fields_retries_transport_failure_then_succeeds() -> None:
    client, provider = make_llm_client(
        [
            LLMProviderError(
                LLMFailureReason.TIMEOUT, "timed out", provider="fake", model="fake-model"
            ),
            llm_response(_SOFTWARE_ENGINEER_FIELDS),
        ],
        config=LLMConfig(max_attempts=2, repair_attempts=0),
    )
    result = extract_profile_fields("raw text", client=client)
    assert result.title == _SOFTWARE_ENGINEER_FIELDS["title"]
    assert len(provider.requests) == 2


def test_extract_profile_fields_raises_llm_provider_error_when_retries_exhausted() -> None:
    error = LLMProviderError(
        LLMFailureReason.CONNECTION_ERROR, "down", provider="fake", model="fake-model"
    )
    client, _ = make_llm_client([error, error], config=LLMConfig(max_attempts=2, repair_attempts=0))
    with pytest.raises(ResumeParsingError) as excinfo:
        extract_profile_fields("raw text", client=client)
    assert excinfo.value.code == ErrorCode.LLM_PROVIDER_ERROR


def test_extract_profile_fields_does_not_retry_non_retryable_transport_error() -> None:
    client, provider = make_llm_client(
        [
            LLMProviderError(
                LLMFailureReason.INVALID_RESPONSE,
                "n/a",
                provider="fake",
                model="fake-model",
            )
        ],
        config=LLMConfig(max_attempts=5, repair_attempts=0),
    )
    with pytest.raises(ResumeParsingError):
        extract_profile_fields("raw text", client=client)
    assert len(provider.requests) == 1


def test_extract_profile_fields_repairs_malformed_json_then_succeeds() -> None:
    client, provider = make_llm_client(
        [
            llm_response_text("not json at all"),
            llm_response(_HR_FIELDS),
        ],
        config=LLMConfig(max_attempts=1, repair_attempts=1),
    )
    result = extract_profile_fields("raw text", client=client)
    assert result.title == _HR_FIELDS["title"]
    assert len(provider.requests) == 2
    # the repair re-prompt must show the model what went wrong
    assert "Problem:" in provider.requests[1].prompt


def test_extract_profile_fields_raises_resume_parse_failed_when_repair_exhausted() -> None:
    client, _ = make_llm_client(
        [llm_response_text("still not json"), llm_response_text("nope")],
        config=LLMConfig(max_attempts=1, repair_attempts=1),
    )
    with pytest.raises(ResumeParsingError) as excinfo:
        extract_profile_fields("raw text", client=client)
    assert excinfo.value.code == ErrorCode.RESUME_PARSE_FAILED


# ---------------------------------------------------------------------------
# parse_resume — end to end (text extraction + LLM extraction + domain
# object assembly)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_resume_builds_candidate_profile() -> None:
    resume = make_resume(file_name="resume.txt")
    client, _ = make_llm_client([llm_response(_MECHANICAL_ENGINEER_FIELDS)])

    raw_text, profile = await parse_resume(
        resume,
        client=client,
        file_content=b"Mechanical engineer resume text.",
    )

    assert raw_text == "Mechanical engineer resume text."
    assert profile.user_id == resume.user_id
    assert profile.resume_id == resume.id
    assert profile.status == ProfileStatus.ACTIVE
    assert profile.title == _MECHANICAL_ENGINEER_FIELDS["title"]
    assert profile.skills == _MECHANICAL_ENGINEER_FIELDS["skills"]
    assert profile.certifications == _MECHANICAL_ENGINEER_FIELDS["certifications"]


@pytest.mark.asyncio
async def test_parse_resume_raises_on_extraction_failure() -> None:
    resume = make_resume(file_name="resume.txt")
    client, _ = make_llm_client(
        [llm_response_text("not json")], config=LLMConfig(max_attempts=1, repair_attempts=0)
    )

    with pytest.raises(ResumeParsingError):
        await parse_resume(resume, client=client, file_content=b"some text")


@pytest.mark.asyncio
async def test_parse_resume_raises_on_unsupported_file_type() -> None:
    resume = make_resume(file_name="resume.exe")
    client, provider = make_llm_client([])

    with pytest.raises(ResumeParsingError) as excinfo:
        await parse_resume(resume, client=client, file_content=b"whatever")
    assert excinfo.value.code == ErrorCode.RESUME_PARSE_FAILED
    assert provider.requests == []  # never reaches the LLM call
