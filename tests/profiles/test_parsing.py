"""Unit tests for the Resume Parsing Workflow (profiles/parsing.py).

No live Ollama server required — every LLM interaction goes through a real
`infrastructure.llm.LLMClient` wired to `ScriptedLLMProvider`
(tests/profiles/conftest.py:make_llm_client). Covers success, failure, and
profession-independence (software engineer, mechanical engineer, HR resumes
all flow through the identical, unbranched extraction path).
"""

from __future__ import annotations

import json

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


# ---------------------------------------------------------------------------
# extract_text — icon-font (Private Use Area) garbage
#
# A real user's resume header decorated the phone/email/location line with
# Font Awesome icons. Icon fonts map their glyphs into the Unicode Private
# Use Area, so text extraction produced private code points wedged straight
# into the real words ("\ue000phone+91 7498618461\ue001..."), and the
# structured-extraction model returned an empty work history from it — the
# user's tailored resume came out with no Experience section at all.
# ---------------------------------------------------------------------------


def test_extract_text_strips_private_use_area_characters() -> None:
    """The real text survives; the private code points do not."""
    content = "\ue000phone+91 7498618461\ue001\ue123ap-\ue0b0arker-altPune".encode()

    text = extract_text("resume.txt", content)

    assert "+91 7498618461" in text
    assert "Pune" in text
    assert not any("\ue000" <= character <= "\uf8ff" for character in text)


def test_extract_text_strips_the_whole_private_use_block() -> None:
    """Both ends of U+E000-U+F8FF, not just the first few code points every
    icon set happens to use.
    """
    content = "\ue000Priya Raman \uefffData \uf8ffAnalyst".encode()

    text = extract_text("resume.txt", content)

    assert text == "Priya Raman Data Analyst"


def test_stripping_a_glyph_does_not_leave_a_double_space() -> None:
    """Icons are usually written as "<glyph> <value>", so a naive removal
    would leave the line littered with double spaces before the model ever
    sees it. Stripping happens before whitespace is collapsed, so it does
    not.
    """
    content = "\ue0b0 Pune, India \ue0b1 priya@example.com".encode()

    text = extract_text("resume.txt", content)

    assert text == "Pune, India priya@example.com"


def test_ordinary_non_ascii_text_is_never_stripped() -> None:
    """The strip is confined to the private-use block: real names, accents
    and non-Latin scripts are ordinary resume content on a platform serving
    every profession and region.
    """
    content = "José Álvarez — Ingeniero Mecánico — 東京 — Ελλάδα".encode()

    text = extract_text("resume.txt", content)

    assert text == "José Álvarez — Ingeniero Mecánico — 東京 — Ελλάδα"


def test_extract_text_rejects_content_that_is_only_icon_glyphs() -> None:
    """Nothing but icons is no resume text at all, and must fail the same
    way an empty file does rather than reach the model as an empty prompt.
    """
    with pytest.raises(ResumeParsingError) as excinfo:
        extract_text("resume.txt", "\ue000\ue001 \ue002".encode())

    assert excinfo.value.code == ErrorCode.RESUME_PARSE_FAILED


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


def test_extract_profile_fields_recovers_from_schema_name_wrapper() -> None:
    """Reproduces the observed local-Ollama failure: the model wraps its
    answer as `{"ExtractedResumeProfile": {...}}` instead of returning the
    bare object, which used to raise SCHEMA_VALIDATION_FAILED ("title:
    Field required") on the first attempt. structured.py's
    `_unwrap_schema_wrapper` now recovers without needing a repair round."""
    wrapped = json.dumps({"ExtractedResumeProfile": _HR_FIELDS})
    client, provider = make_llm_client(
        [llm_response_text(wrapped)],
        config=LLMConfig(max_attempts=1, repair_attempts=0),
    )
    result = extract_profile_fields("raw text", client=client)
    assert result.title == _HR_FIELDS["title"]
    # recovered on the first attempt, no repair round needed
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


# ---------------------------------------------------------------------------
# contact + work_history extraction (docs/tailored-resume/TECH.md Task 1)
# ---------------------------------------------------------------------------

# A realistic resume that genuinely states contact details and several
# positions — deliberately non-software (the platform is
# profession-independent) and deliberately written the way a real resume is,
# with a current role whose end date is "Present".
_OPERATIONS_RESUME_TEXT = """
PRIYA RAMESH
Chennai, Tamil Nadu, India
priya.ramesh@example.com | +91 98765 43210

PROFESSIONAL SUMMARY
Operations manager with nine years leading warehouse and last-mile
logistics teams across South India.

EXPERIENCE

Regional Operations Manager, Sundara Logistics Pvt Ltd
March 2021 - Present
- Run three distribution centres covering 140 daily delivery routes.
- Cut average dispatch turnaround from 6.2 hours to 3.8 hours.
- Manage a team of 54 across shift supervisors, loaders and drivers.

Warehouse Supervisor, Kaveri Freight Services
June 2017 - February 2021
- Owned inbound receiving and put-away for a 90,000 sq ft facility.
- Introduced a cycle-count programme that reduced stock variance to 0.4%.

Logistics Coordinator, Meenakshi Traders
August 2015 - May 2017
- Scheduled outbound shipments and reconciled carrier invoices.

EDUCATION
B.Com, University of Madras, 2015

SKILLS
Warehouse operations, Route planning, SAP EWM, Team leadership
"""

# What the model is expected to return for the resume above.
_OPERATIONS_FIELDS = {
    "title": "Regional Operations Manager",
    "summary": "Operations manager with nine years leading logistics teams.",
    "contact": {
        "full_name": "Priya Ramesh",
        "email": "priya.ramesh@example.com",
        "phone": "+91 98765 43210",
        "location": "Chennai, Tamil Nadu, India",
    },
    "skills": ["Warehouse operations", "Route planning", "SAP EWM", "Team leadership"],
    "experience_years": 9.0,
    "seniority": "Manager",
    "education": [
        {
            "institution": "University of Madras",
            "degree": "B.Com",
            "field_of_study": None,
            "graduation_year": 2015,
        }
    ],
    "work_history": [
        {
            "company": "Sundara Logistics Pvt Ltd",
            "title": "Regional Operations Manager",
            "start_date": "March 2021",
            "end_date": None,  # "Present" — current role
            "bullets": [
                "Run three distribution centres covering 140 daily delivery routes.",
                "Cut average dispatch turnaround from 6.2 hours to 3.8 hours.",
                "Manage a team of 54 across shift supervisors, loaders and drivers.",
            ],
        },
        {
            "company": "Kaveri Freight Services",
            "title": "Warehouse Supervisor",
            "start_date": "June 2017",
            "end_date": "February 2021",
            "bullets": [
                "Owned inbound receiving and put-away for a 90,000 sq ft facility.",
                "Introduced a cycle-count programme that reduced stock variance to 0.4%.",
            ],
        },
        {
            "company": "Meenakshi Traders",
            "title": "Logistics Coordinator",
            "start_date": "August 2015",
            "end_date": "May 2017",
            "bullets": ["Scheduled outbound shipments and reconciled carrier invoices."],
        },
    ],
    "certifications": [],
    "projects": [],
    "industries": ["Logistics"],
    "target_roles": ["Operations Manager", "Logistics Manager"],
}

# A resume that is genuinely nothing but a skills list — no name, no email,
# no phone, no location, no employers. The model must return null contact
# rather than manufacturing one.
_SKILLS_ONLY_RESUME_TEXT = """
SKILLS
Financial modelling
IFRS reporting
Budget forecasting
Advanced Excel
Power BI
Stakeholder presentations
"""

_SKILLS_ONLY_FIELDS = {
    "title": "Finance Professional",
    "summary": None,
    "contact": None,
    "skills": [
        "Financial modelling",
        "IFRS reporting",
        "Budget forecasting",
        "Advanced Excel",
        "Power BI",
        "Stakeholder presentations",
    ],
    "experience_years": None,
    "seniority": None,
    "education": [],
    "work_history": [],
    "certifications": [],
    "projects": [],
    "industries": [],
    "target_roles": [],
}


@pytest.mark.asyncio
async def test_parse_resume_populates_contact_and_work_history() -> None:
    """A resume with real contact details and a real employment history
    lands both on the `CandidateProfile`, with the values the model actually
    extracted — not defaults."""
    resume = make_resume(file_name="resume.txt")
    client, _ = make_llm_client([llm_response(_OPERATIONS_FIELDS)])

    _, profile = await parse_resume(
        resume,
        client=client,
        file_content=_OPERATIONS_RESUME_TEXT.encode(),
    )

    assert profile.contact is not None
    assert profile.contact.full_name == "Priya Ramesh"
    assert profile.contact.email == "priya.ramesh@example.com"
    assert profile.contact.phone == "+91 98765 43210"
    assert profile.contact.location == "Chennai, Tamil Nadu, India"

    assert len(profile.work_history) == 3
    current, previous, first = profile.work_history

    assert current.company == "Sundara Logistics Pvt Ltd"
    assert current.title == "Regional Operations Manager"
    assert current.start_date == "March 2021"
    # "Present" must arrive as None, which is how a current role is encoded
    assert current.end_date is None
    assert len(current.bullets) == 3
    assert "140 daily delivery routes" in current.bullets[0]

    assert previous.company == "Kaveri Freight Services"
    assert previous.end_date == "February 2021"
    assert previous.bullets[1].endswith("stock variance to 0.4%.")

    assert first.title == "Logistics Coordinator"
    assert first.bullets == [
        "Scheduled outbound shipments and reconciled carrier invoices."
    ]


@pytest.mark.asyncio
async def test_parse_resume_leaves_contact_none_when_resume_has_none() -> None:
    """A bare skills list has no discoverable contact details. `contact`
    must stay `None` — a fabricated name/email would be worse than an empty
    one, since it ends up on a document the user submits."""
    resume = make_resume(file_name="resume.txt")
    client, _ = make_llm_client([llm_response(_SKILLS_ONLY_FIELDS)])

    _, profile = await parse_resume(
        resume,
        client=client,
        file_content=_SKILLS_ONLY_RESUME_TEXT.encode(),
    )

    assert profile.contact is None
    assert profile.work_history == []
    # the rest of the profile still parses normally
    assert "IFRS reporting" in profile.skills


def test_extraction_prompt_asks_for_real_contact_and_work_history() -> None:
    """The single extraction call must actually ask for both new fields,
    forbid inventing contact details, and stay profession-agnostic while
    doing so."""
    client, provider = make_llm_client([llm_response(_OPERATIONS_FIELDS)])
    extract_profile_fields(_OPERATIONS_RESUME_TEXT, client=client)

    # still exactly one LLM call — enriched schema, not a second pass
    assert len(provider.requests) == 1
    prompt = provider.requests[0].prompt.lower()

    assert '"contact"' in prompt
    assert '"work_history"' in prompt
    assert "never invent, guess, or complete a contact detail" in prompt
    assert "do not treat \"work history\" as meaning only" in prompt
    assert "end_date must be null when the role is current" in prompt


# ---------------------------------------------------------------------------
# contact links (LinkedIn / GitHub / portfolio)
# ---------------------------------------------------------------------------

# A resume that genuinely prints all three links. Deliberately not a
# software engineer: "GitHub" here belongs to a researcher publishing
# analysis code, and the portfolio is an ORCID record — the extraction rule
# must not be a technology-sector rule.
_LINKED_RESUME_TEXT = """
DR. HANNAH OKONKWO
Manchester, UK
hannah.okonkwo@example.ac.uk | +44 161 496 0000
linkedin.com/in/hannah-okonkwo | github.com/hokonkwo | orcid.org/0000-0002-1825-0097

PROFESSIONAL SUMMARY
Epidemiologist with eleven years in population health research.

EXPERIENCE
Senior Epidemiologist, Northern Health Institute
September 2019 - Present
- Led a cohort study of 40,000 participants on respiratory outcomes.

EDUCATION
PhD Epidemiology, University of Manchester, 2014
"""

_LINKED_FIELDS = {
    "title": "Senior Epidemiologist",
    "summary": "Epidemiologist with eleven years in population health research.",
    "contact": {
        "full_name": "Dr. Hannah Okonkwo",
        "email": "hannah.okonkwo@example.ac.uk",
        "phone": "+44 161 496 0000",
        "location": "Manchester, UK",
        "linkedin_url": "linkedin.com/in/hannah-okonkwo",
        "github_url": "github.com/hokonkwo",
        "portfolio_url": "orcid.org/0000-0002-1825-0097",
    },
    "skills": ["Cohort study design", "Stata", "Survival analysis"],
    "experience_years": 11.0,
    "seniority": "Senior",
    "education": [],
    "work_history": [],
    "certifications": [],
    "projects": [],
    "industries": ["Public Health"],
    "target_roles": ["Epidemiologist"],
}


@pytest.mark.asyncio
async def test_parse_resume_populates_all_three_contact_links() -> None:
    """A resume that prints LinkedIn, GitHub, and a portfolio link lands all
    three on the profile, verbatim — they end up as real hyperlinks in the
    generated resume, so a mangled one is a broken document."""
    resume = make_resume(file_name="resume.txt")
    client, _ = make_llm_client([llm_response(_LINKED_FIELDS)])

    _, profile = await parse_resume(
        resume, client=client, file_content=_LINKED_RESUME_TEXT.encode()
    )

    assert profile.contact is not None
    assert profile.contact.linkedin_url == "linkedin.com/in/hannah-okonkwo"
    assert profile.contact.github_url == "github.com/hokonkwo"
    assert profile.contact.portfolio_url == "orcid.org/0000-0002-1825-0097"
    # the rest of the contact block is unaffected
    assert profile.contact.full_name == "Dr. Hannah Okonkwo"
    assert profile.contact.email == "hannah.okonkwo@example.ac.uk"


@pytest.mark.asyncio
async def test_parse_resume_leaves_all_three_links_none_when_absent() -> None:
    """The common case: a resume with a full contact block and no links at
    all. All three must stay `None` — a URL guessed from a name would be
    published on a document the user actually submits."""
    resume = make_resume(file_name="resume.txt")
    client, _ = make_llm_client([llm_response(_OPERATIONS_FIELDS)])

    _, profile = await parse_resume(
        resume, client=client, file_content=_OPERATIONS_RESUME_TEXT.encode()
    )

    assert profile.contact is not None
    assert profile.contact.linkedin_url is None
    assert profile.contact.github_url is None
    assert profile.contact.portfolio_url is None
    # and the details the resume *does* state are still there
    assert profile.contact.full_name == "Priya Ramesh"
    assert profile.contact.email == "priya.ramesh@example.com"


@pytest.mark.asyncio
async def test_parse_resume_with_no_contact_block_has_no_links_either() -> None:
    resume = make_resume(file_name="resume.txt")
    client, _ = make_llm_client([llm_response(_SKILLS_ONLY_FIELDS)])

    _, profile = await parse_resume(
        resume, client=client, file_content=_SKILLS_ONLY_RESUME_TEXT.encode()
    )

    assert profile.contact is None


def test_extraction_prompt_asks_for_the_three_links_without_inviting_guesses() -> None:
    """One prompt, one call: the links are part of the existing contact
    block, and the anti-fabrication rule is stated for URLs specifically —
    "linkedin.com/in/<name>" is the exact failure mode to forbid."""
    client, provider = make_llm_client([llm_response(_LINKED_FIELDS)])
    extract_profile_fields(_LINKED_RESUME_TEXT, client=client)

    assert len(provider.requests) == 1
    prompt = provider.requests[0].prompt.lower()

    assert '"linkedin_url"' in prompt
    assert '"github_url"' in prompt
    assert '"portfolio_url"' in prompt
    assert "never construct, complete, or guess a url" in prompt
    assert "linkedin.com/in/<name>" in prompt
    assert "only if the resume literally prints it" in prompt
    # still profession-independent about it
    assert "the three url fields apply to every profession" in prompt


def test_contact_links_round_trip_through_the_extraction_schema() -> None:
    """The links reach the profile through `ExtractedResumeProfile`, so the
    schema the model is validated against must carry them."""
    extracted = ExtractedResumeProfile(**_LINKED_FIELDS)

    assert extracted.contact is not None
    assert extracted.contact.github_url == "github.com/hokonkwo"


def test_extracted_profile_defaults_keep_new_fields_optional() -> None:
    """Older/shorter model responses that omit the new keys entirely must
    still validate — `contact` absent means None, `work_history` absent
    means []."""
    minimal = ExtractedResumeProfile(title="Registered Nurse")
    assert minimal.contact is None
    assert minimal.work_history == []

    # ... and a contact block that omits the link keys entirely still
    # validates, which is what keeps every profile parsed before those keys
    # existed readable (`candidate_profiles.contact` is a JSON column, so
    # there is no migration to make the old rows carry them).
    from shared.types.dto import ContactInfo

    legacy = ContactInfo(**{"full_name": "Aoife Devlin", "email": "a@example.com"})
    assert legacy.linkedin_url is None
    assert legacy.github_url is None
    assert legacy.portfolio_url is None


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
