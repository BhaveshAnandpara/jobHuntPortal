"""Resume parsing — internal background step, not a LangGraph workflow.
See docs/architecture/langgraph-state.md#what-is-deliberately-not-a-langgraph-workflow
and
docs/architecture/component-contracts.md#resume-parsing-workflow-internal-background-step-not-langgraph--see-langgraph-statemd.

Trigger: new Resume row with status=PARSING (enqueued by the upload handler
in profiles/api/). Steps: text extraction from the stored file -> LLM
Provider Layer structured extraction -> a new `CandidateProfile`.
Persistence (repository writes) and eventing (`profiles.updated`) are the
caller's responsibility — see profiles/service.py, which is what actually
implements the contract's DB-change and Kafka-emit steps around this
module's pure `parse_resume` function.

Deliberately profession-independent throughout: every extracted field is
free text or a free-text list, exactly like `CandidateProfile`
(docs/architecture/domain-model.md#candidateprofile) — no branch here reads
or infers "software engineer" (or any other single profession) specially.

If parsing later grows branching/routing logic (e.g. profession-specific
extraction passes), it becomes a fourth typed LangGraph state at that
point — not before.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from infrastructure.llm import LLMClient
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from shared.errors.codes import ErrorCode
from shared.types.domain.candidate_profile import CandidateProfile
from shared.types.domain.resume import Resume
from shared.types.dto import EducationEntry
from shared.types.enums import ProfileStatus
from shared.types.ids import ProfileId

# Files supported for upload/parsing, per
# docs/architecture/component-contracts.md#post-resumes-upload
# ("file type in {pdf, docx, txt}").
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}

_CONTROL_CHARS_RE = re.compile(r"[^\x20-\x7E\n\t]")
_WHITESPACE_RE = re.compile(r"[ \t]+")

_SYSTEM_PROMPT = (
    "You are a resume-analysis assistant used by a career platform that "
    "supports every profession — software engineering, mechanical "
    "engineering, HR, finance, design, and any other field. Never assume "
    "the candidate is a software engineer or any other specific "
    "profession. Infer only what the resume text actually supports."
)


class ResumeParsingError(Exception):
    """Typed parsing failure carrying a shared `ErrorCode`
    (`RESUME_PARSE_FAILED` or `LLM_PROVIDER_ERROR`, per
    component-contracts.md's Resume Parsing Workflow entry), which the
    caller maps onto `Resume.status = PARSE_FAILED` /
    `Resume.parse_error`.
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ExtractedResumeProfile(BaseModel):
    """LLM structured-output shape for one resume.

    Internal to the parsing workflow only — mapped onto the canonical
    `CandidateProfile` domain type once validated; this type itself never
    crosses a component boundary. Field-for-field, it mirrors the
    profession-independent, free-text/list fields of `CandidateProfile`
    (docs/architecture/domain-model.md#candidateprofile), deliberately
    excluding the identity/lifecycle fields (`id`, `user_id`, `resume_id`,
    `status`, `generated_at`) that the parsing workflow fills in itself
    rather than asking the model to invent.
    """

    title: str
    summary: str | None = None
    skills: list[str] = []
    experience_years: float | None = None
    seniority: str | None = None
    education: list[EducationEntry] = []
    certifications: list[str] = []
    projects: list[str] = []
    industries: list[str] = []
    target_roles: list[str] = []


def extract_text(file_name: str, content: bytes) -> str:
    """Best-effort text extraction, dependency-light by design.

    `.txt` is a direct UTF-8 decode. `.pdf`/`.docx` fall back to decoding
    the raw bytes and stripping non-printable/control bytes rather than a
    real structural parse: `pyproject.toml` does not (yet) declare a
    PDF/DOCX parsing library (e.g. `pypdf`, `python-docx`) as a dependency
    for this component, and the brief for this pass is to "keep extraction
    pragmatic rather than adding a new dependency". This is good enough to
    hand usable prose through to the LLM extraction step for typical
    text-heavy resumes; swapping in a real parser later is an additive,
    isolated change to this one function.
    """
    suffix = Path(file_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ResumeParsingError(
            ErrorCode.RESUME_PARSE_FAILED,
            f"unsupported file type: {suffix or '(none)'}",
        )

    if suffix == ".txt":
        text = content.decode("utf-8", errors="replace")
    else:
        text = content.decode("utf-8", errors="ignore")
        text = _CONTROL_CHARS_RE.sub(" ", text)

    text = _WHITESPACE_RE.sub(" ", text).strip()
    if not text:
        raise ResumeParsingError(ErrorCode.RESUME_PARSE_FAILED, "extracted text is empty")
    return text


def _build_extraction_prompt(raw_text: str) -> str:
    return f"""
    You are a strict resume information extraction system.

    Extract ONE structured professional profile from the resume text below.

    IMPORTANT RULES:

    1. Use only information supported by the resume.
    2. Do not guess or fabricate information.
    3. The candidate may belong to ANY profession or industry.
    4. Do not assume software engineering or technology.
    5. If a scalar field cannot be determined, return null.
    6. If a list field has no supported values, return [].
    7. Preserve the candidate's own professional terminology where possible.
    8. Read the ENTIRE resume before answering.

    You must return exactly ONE object with these fields:

    {{
    "title": string | null,
    "summary": string | null,
    "skills": [string],
    "experience_years": number | null,
    "seniority": string | null,
    "education": [
        {{
        "institution": string | null,
        "degree": string | null,
        "field_of_study": string | null,
        "graduation_year": integer | null
        }}
    ],
    "certifications": [string],
    "projects": [string],
    "industries": [string],
    "target_roles": [string]
    }}

    FIELD RULES:

    title
    - A short professional identity supported by the resume.
    - Prefer an explicitly stated current/recent role.
    - If no professional identity can be established, return null.
    - Do not use section names such as "Summary", "Experience", or "Skills".

    summary
    - A concise 1-3 sentence professional summary based only on the resume.
    - Do not invent achievements or experience.
    - Return null if there is not enough information.

    skills
    - Include actual skills, tools, technologies, methods, domain competencies,
    or professional capabilities explicitly supported by the resume.
    - Do not include company names, job titles, section headings, or education
    institutions.
    - Return [] if none are supported.

    experience_years
    - Return total relevant professional experience only when reasonably
    supported by explicit employment dates/durations.
    - Do not guess from graduation year.
    - Do not count overlapping jobs twice.
    - Return null when it cannot be determined reliably.

    seniority
    - Use a level supported by explicit roles or clearly supported experience.
    - Examples: Entry, Junior, Mid-level, Senior, Lead, Principal, Manager.
    - Do not infer "Senior" merely because the candidate has several skills.
    - Return null if unclear.

    education
    - Extract education entries only.
    - Do not treat certifications or work experience as education.
    - Use null for missing fields within an education entry.

    certifications
    - Include only explicitly stated professional certifications.
    - Return [] if none exist.

    projects
    - Include notable projects explicitly described in the resume.
    - Keep each item concise.
    - Return [] if none are present.

    industries
    - Include industries explicitly supported by the candidate's work,
    education, or projects.
    - Do not guess broad industries from individual tools.
    - Return [] if unclear.

    target_roles
    - Include roles strongly supported by the candidate's existing experience,
    skills, and resume positioning.
    - Do not invent aspirational careers unrelated to the resume.
    - Return [] if insufficient information exists.

    CRITICAL OUTPUT RULES:

    - Return ONLY the object itself.
    - Do NOT wrap it inside "ExtractedResumeProfile".
    - Do NOT wrap it inside "profile", "result", "data", or any other key.
    - Do NOT return markdown.
    - Do NOT return ```json.
    - Do NOT return explanations.
    - The top-level object MUST contain the field "title".
    - Missing scalar values must be null.
    - Missing list values must be [].

    RESUME TEXT:
    ----------------
    {raw_text}
    ----------------
    """

# LLMFailureReason values that mean "the model responded, but not with a
# schema-conforming JSON object" — mapped to RESUME_PARSE_FAILED. Every other
# reason (timeout, connection error, generic provider error) is a transport
# failure, mapped to LLM_PROVIDER_ERROR. `LLMClient.complete_structured`
# already owns both retry budgets (`LLMConfig.max_attempts` for transport,
# `LLMConfig.repair_attempts` for structured-output repair — see
# infrastructure/llm/structured.py); this module only translates its one
# exception type onto the two error codes component-contracts.md documents
# for this workflow.
_STRUCTURED_OUTPUT_FAILURE_REASONS = frozenset(
    {LLMFailureReason.INVALID_RESPONSE, LLMFailureReason.SCHEMA_VALIDATION_FAILED}
)


def extract_profile_fields(raw_text: str, *, client: LLMClient) -> ExtractedResumeProfile:
    """Run the LLM Provider Layer's `LLMClient` to turn resume text into an
    `ExtractedResumeProfile`.

    Raises `ResumeParsingError` with `ErrorCode.LLM_PROVIDER_ERROR` for
    transport failures and `ErrorCode.RESUME_PARSE_FAILED` when the model
    never produces a schema-conforming response within `client`'s repair
    budget — matching the two error codes component-contracts.md documents
    for this workflow.
    """
    prompt = _build_extraction_prompt(raw_text)
    try:
        return client.complete_structured(prompt, ExtractedResumeProfile, system=_SYSTEM_PROMPT)
    except LLMProviderError as exc:
        code = (
            ErrorCode.RESUME_PARSE_FAILED
            if exc.reason in _STRUCTURED_OUTPUT_FAILURE_REASONS
            else ErrorCode.LLM_PROVIDER_ERROR
        )
        raise ResumeParsingError(code, str(exc)) from exc


async def parse_resume(
    resume: Resume,
    *,
    client: LLMClient,
    file_content: bytes,
) -> tuple[str, CandidateProfile]:
    """The Resume Parsing Workflow's pure step: raw file bytes -> raw text
    + a new `CandidateProfile`.

    Persistence (`resumes`/`candidate_profiles` writes) and eventing
    (`profiles.updated`) are deliberately not done here — see
    `profiles/service.py:ProfileService.run_parsing_workflow`, which
    surrounds this function with the DB writes and event publish that
    component-contracts.md's Resume Parsing Workflow entry specifies.
    Keeping this function pure (no repository/producer dependency) is what
    makes it directly unit-testable with a fake `LLMProvider` injected into
    an `LLMClient`, and no database or Kafka broker.

    Raises `ResumeParsingError` on any extraction failure; the caller is
    responsible for translating that into `Resume.status = PARSE_FAILED`.
    """
    raw_text = extract_text(resume.file_name, file_content)
    fields = await asyncio.to_thread(extract_profile_fields, raw_text, client=client)

    profile = CandidateProfile(
        id=ProfileId(uuid4()),
        user_id=resume.user_id,
        resume_id=resume.id,
        title=fields.title,
        summary=fields.summary,
        skills=fields.skills,
        experience_years=fields.experience_years,
        seniority=fields.seniority,
        education=fields.education,
        certifications=fields.certifications,
        projects=fields.projects,
        industries=fields.industries,
        target_roles=fields.target_roles,
        status=ProfileStatus.ACTIVE,
        generated_at=datetime.now(UTC),
    )
    return raw_text, profile


__all__ = [
    "SUPPORTED_EXTENSIONS",
    "ExtractedResumeProfile",
    "ResumeParsingError",
    "extract_profile_fields",
    "extract_text",
    "parse_resume",
]
