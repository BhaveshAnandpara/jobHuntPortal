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
import io
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from docx import Document
from pydantic import BaseModel
from pypdf import PdfReader

from infrastructure.llm import LLMClient
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError, excerpt
from infrastructure.logging import format_context, get_logger
from shared.errors.codes import ErrorCode
from shared.types.domain.candidate_profile import CandidateProfile
from shared.types.domain.resume import Resume
from shared.types.dto import ContactInfo, EducationEntry, WorkExperienceEntry
from shared.types.enums import ProfileStatus
from shared.types.ids import ProfileId

logger = get_logger(__name__)

# Files supported for upload/parsing, per
# docs/architecture/component-contracts.md#post-resumes-upload
# ("file type in {pdf, docx, txt}").
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}

_WHITESPACE_RE = re.compile(r"[ \t]+")

# Unicode Private Use Area. Icon fonts (Font Awesome and friends) map their
# glyphs into this block, so a resume whose header decorates the phone /
# email / location line with icons extracts as those private code points
# jammed against the real text — a real user's PDF produced
# "\uf095phone+91 7498618461\uf0e0..." rather than "phone +91 ...", and the
# structured-extraction model returned an empty work history from it.
#
# The characters carry no textual meaning by definition (the code points are
# unassigned; only the embedding font knows what they draw), so dropping
# them loses nothing and is safe for every profession and language. Stripped
# *before* `_WHITESPACE_RE` collapses runs of spaces, so removing a glyph
# that sat between two words cannot leave a double space behind.
_PRIVATE_USE_AREA_RE = re.compile(r"[\ue000-\uf8ff]")

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
    contact: ContactInfo | None = None
    skills: list[str] = []
    experience_years: float | None = None
    seniority: str | None = None
    education: list[EducationEntry] = []
    work_history: list[WorkExperienceEntry] = []
    certifications: list[str] = []
    projects: list[str] = []
    industries: list[str] = []
    target_roles: list[str] = []


def extract_text(file_name: str, content: bytes) -> str:
    """Real structural text extraction per file type.

    `.txt` is a direct UTF-8 decode. `.pdf` uses `pypdf` (per-page text,
    joined). `.docx` uses `python-docx` (paragraph text, joined). Whatever
    the source, Private Use Area characters are stripped before the text is
    returned (`_PRIVATE_USE_AREA_RE`) — icon fonts render through that block
    and would otherwise arrive as garbage wedged into real words. This is
    the single normalization point for everything downstream: the returned
    string is both what is persisted as `Resume.raw_text` and what
    `extract_profile_fields` sends to the model.

    Both format branches replace an earlier placeholder that raw-decoded the file's bytes as
    UTF-8: PDF/DOCX are binary formats (compressed streams, XML container
    structure), so that produced unusable noise instead of the resume's
    actual prose — invisible while Ollama was failing outright on
    timeouts/schema-echoing, only surfaced once a provider swap (Gemini)
    started returning clean, fast responses built from that noise.
    """
    suffix = Path(file_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ResumeParsingError(
            ErrorCode.RESUME_PARSE_FAILED,
            f"unsupported file type: {suffix or '(none)'}",
        )

    if suffix == ".txt":
        text = content.decode("utf-8", errors="replace")
    elif suffix == ".pdf":
        text = _extract_pdf_text(content)
    else:
        text = _extract_docx_text(content)

    text = _PRIVATE_USE_AREA_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if not text:
        raise ResumeParsingError(ErrorCode.RESUME_PARSE_FAILED, "extracted text is empty")

    logger.info(
        "Resume text extracted | %s",
        format_context(file_name=file_name, file_type=suffix, chars=len(text)),
    )
    return text


def _extract_pdf_text(content: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise ResumeParsingError(ErrorCode.RESUME_PARSE_FAILED, f"could not read PDF: {exc}") from exc
    logger.info("PDF text extracted | %s", format_context(pages=len(pages)))
    return "\n\n".join(pages)


def _extract_docx_text(content: bytes) -> str:
    try:
        document = Document(io.BytesIO(content))
        paragraphs = [p.text for p in document.paragraphs]
    except Exception as exc:
        raise ResumeParsingError(ErrorCode.RESUME_PARSE_FAILED, f"could not read DOCX: {exc}") from exc
    logger.info("DOCX text extracted | %s", format_context(paragraphs=len(paragraphs)))
    return "\n".join(paragraphs)


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
    "contact": {{
        "full_name": string | null,
        "email": string | null,
        "phone": string | null,
        "location": string | null,
        "linkedin_url": string | null,
        "github_url": string | null,
        "portfolio_url": string | null
        }} | null,
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
    "work_history": [
        {{
        "company": string,
        "title": string,
        "start_date": string | null,
        "end_date": string | null,
        "bullets": [string]
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

    contact
    - Extract the candidate's OWN contact details, exactly as written in the
    resume text. Copy them verbatim; do not reformat, normalize, or correct
    them.
    - full_name: the candidate's own name, usually at the top of the resume.
    Do not use a referee's, manager's, or employer's name.
    - email: an email address belonging to the candidate. Do not use a
    company/support address quoted inside a job description.
    - phone: a phone number belonging to the candidate, with whatever
    country/area formatting the resume uses.
    - location: where the candidate is based (city / state / country as
    written). Do not use an employer's or university's location unless the
    resume presents it as the candidate's own.
    - linkedin_url: the candidate's own LinkedIn profile link, only if the
    resume literally prints it (e.g. "linkedin.com/in/priya-ramesh").
    - github_url: the candidate's own GitHub (or equivalent public code
    hosting) profile link, only if the resume literally prints it.
    - portfolio_url: the candidate's own portfolio, personal site, or
    professional profile link that is neither LinkedIn nor GitHub (a
    designer's Behance, a writer's personal site, a researcher's ORCID),
    only if the resume literally prints it.
    - The three URL fields apply to EVERY profession. Most resumes print
    none of them; that is normal and must be reported as null, not filled in.
    - NEVER construct, complete, or guess a URL. Do not turn a name into
    "linkedin.com/in/<name>", do not turn a username mentioned elsewhere into
    a profile URL, and do not expand a bare word like "LinkedIn" or
    "Portfolio" into an address. If the resume text does not contain the
    link itself, return null for that field.
    - Copy each URL exactly as written, including or omitting "https://"
    exactly as the resume does.
    - NEVER invent, guess, or complete a contact detail that is not literally
    present in the resume text. An email must not be constructed from a name;
    a location must not be inferred from an employer or school.
    - Set any individual field you cannot find to null.
    - If NONE of the seven fields are present in the resume, return null for
    the whole "contact" object.

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

    work_history
    - Extract ONE entry per position/engagement the candidate actually held,
    in the order the resume presents them (most recent first if the resume
    does).
    - This applies to EVERY profession: a factory shift supervisor, a nurse,
    a teacher, an accountant, a freelance designer, and a software engineer
    all have work history. Do not treat "work history" as meaning only
    corporate technology jobs.
    - company: the employer, client, practice, agency, institution, or
    organization as named in the resume.
    - title: the role/position held there, in the candidate's own wording.
    - start_date / end_date: copy the dates exactly as the resume writes them
    (e.g. "Jan 2020", "2020", "03/2019"). Do not convert between formats and
    do not invent a date that is not stated — use null instead.
    - end_date MUST be null when the role is current (the resume says
    "Present", "Current", "Now", "to date", or leaves the end open).
    - bullets: the achievement/responsibility lines stated under that
    position, one string per line, in the resume's own words. Do not merge
    several bullets into one, do not summarize them away, and do not write
    new ones the resume does not support. Return [] if the entry has no
    bullet lines.
    - Do not create an entry for education, a certification, a personal
    project with no employer, or a skills section heading.
    - Return [] if the resume describes no positions at all.

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


def _checked_contact(contact: ContactInfo | None) -> ContactInfo | None:
    """Stamp `links_checked = True` on a contact a real extraction produced.

    Called by `parse_resume` and `enrich_profile` — the two functions that
    turn an extraction into a `CandidateProfile` — so every contact this
    component ever hands out or persists carries the flag, and it is set
    unconditionally, whether or not the model actually found a
    LinkedIn/GitHub/portfolio link.

    That unconditional part is the whole point. The flag records that the
    current prompt (which does ask for all three links) has run over this
    resume, which is what lets `profile_needs_enrichment` tell "we looked
    and this resume prints no links" apart from "nothing ever looked".
    Setting it only when a link was found would re-parse every link-less
    resume on every request, forever.

    `None` stays `None`: a resume with no contact details at all yields no
    `ContactInfo` to flag, and that case is already covered by the other
    half of `profile_needs_enrichment`.

    The model's own value for the field is overwritten rather than trusted —
    it is a bookkeeping fact about this pipeline, not something extracted
    from a resume.
    """
    if contact is None:
        return None
    return contact.model_copy(update={"links_checked": True})


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
    logger.info(
        "LLM extraction started | %s",
        format_context(resume_text_chars=len(raw_text), prompt_chars=len(prompt)),
    )
    try:
        fields = client.complete_structured(prompt, ExtractedResumeProfile, system=_SYSTEM_PROMPT)
    except LLMProviderError as exc:
        code = (
            ErrorCode.RESUME_PARSE_FAILED
            if exc.reason in _STRUCTURED_OUTPUT_FAILURE_REASONS
            else ErrorCode.LLM_PROVIDER_ERROR
        )
        logger.error(
            "LLM extraction failed | %s",
            format_context(
                reason=exc.reason.value,
                attempts=exc.attempts,
                raw_text=excerpt(exc.raw_text) if exc.raw_text else None,
            ),
        )
        raise ResumeParsingError(code, str(exc)) from exc

    logger.info(
        "LLM extraction succeeded | %s",
        format_context(
            title=fields.title,
            skills=len(fields.skills),
            education=len(fields.education),
            work_history=len(fields.work_history),
            # count only — contact details are personal data and must not be
            # written to logs
            has_contact=fields.contact is not None,
            certifications=len(fields.certifications),
            projects=len(fields.projects),
        ),
    )
    return fields


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
        contact=_checked_contact(fields.contact),
        skills=fields.skills,
        experience_years=fields.experience_years,
        seniority=fields.seniority,
        education=fields.education,
        work_history=fields.work_history,
        certifications=fields.certifications,
        projects=fields.projects,
        industries=fields.industries,
        target_roles=fields.target_roles,
        status=ProfileStatus.ACTIVE,
        generated_at=datetime.now(UTC),
    )
    return raw_text, profile


def profile_needs_enrichment(profile: CandidateProfile) -> bool:
    """True when `profile` predates part of the current extraction and must
    be re-parsed before anything can read those fields.

    Two generations of profile qualify, and they are recognised differently
    because only one of them can be recognised from the data alone.

    **Pre-contact/work-history rows: `contact is None and not
    work_history`.** A profile parsed before either field existed has both
    at their column defaults, because the extraction prompt of the day
    never asked for either. That joint emptiness is the only available
    signature of such a row.

    The check is `and`, not `or`, on purpose. A resume parsed *after* those
    fields shipped may legitimately yield only one of them — a CV listing
    six jobs but no email address, or a one-page bio with contact details
    and no itemized employment history. Requiring *both* to be empty means
    those profiles are read as already-parsed (correctly, they were) rather
    than re-run through the LLM on every request just because the resume
    genuinely had nothing to say for one field. The trade-off: a
    pre-feature profile whose resume contains neither contact details nor
    any employment history is re-parsed each time it is asked for, costing
    an LLM call rather than correctness.

    **Pre-links rows: `contact is not None and not contact.links_checked`.**
    `linkedin_url`/`github_url`/`portfolio_url` shipped later still, so a
    profile parsed in between carries a populated `contact` (name, email,
    phone, location) whose three link fields are `None` purely because
    nothing ever looked for them. Those rows are invisible to the clause
    above — `contact` is not `None` — which is exactly the gap that left a
    real user's tailored resume with no LinkedIn/GitHub hyperlinks and no
    way to ever acquire them.

    This clause tests the flag, never the link values, and that is what
    keeps it from looping. `ContactInfo.links_checked` defaults to `False`,
    so every row written before it existed reads as "never looked" and is
    re-parsed exactly once; `extract_profile_fields` then stamps it `True`
    whether or not the resume actually printed any links, so a candidate
    with no public links is checked once and settles, with all three fields
    honestly `None`. Had the condition instead been "all three links are
    `None`", that same candidate would be re-parsed on every single request
    for the rest of the profile's life.
    """
    contact = profile.contact
    if contact is None:
        return not profile.work_history
    return not contact.links_checked


async def enrich_profile(
    profile: CandidateProfile, raw_text: str, *, client: LLMClient
) -> CandidateProfile:
    """Re-run the extraction prompt over `raw_text` and return a copy of
    `profile` carrying the freshly extracted `contact`/`work_history`.

    Pure, exactly like `parse_resume`: no repository, no producer, no
    storage — persistence is `ProfileService.ensure_enriched_profile`'s
    job. Only the two fields are taken from the new extraction; `title`,
    `summary`, `skills`, `education`, `certifications`, `projects`,
    `industries`, `target_roles`, `status` and `generated_at` are carried
    over from the existing profile untouched, so a backfill can never
    silently rewrite data a user has already seen — the same LLM asked the
    same question twice does not necessarily give the same answer, and this
    operation is a *backfill*, not a re-parse of the whole profile.

    The returned `contact`, when there is one, carries
    `links_checked=True` (`_checked_contact`) — which is what stops this
    backfill from running again on the next request for a candidate whose
    resume genuinely prints no LinkedIn/GitHub/portfolio link.

    Runs the blocking `extract_profile_fields` on a worker thread for the
    same reason `parse_resume` does: `LLMClient.complete_structured` is
    synchronous and would otherwise block the event loop.

    Raises `ResumeParsingError` on any extraction failure.
    """
    fields = await asyncio.to_thread(extract_profile_fields, raw_text, client=client)
    logger.info(
        "Profile enrichment extracted | %s",
        format_context(
            profile_id=profile.id,
            resume_id=profile.resume_id,
            work_history=len(fields.work_history),
            # count/flag only — contact details are personal data
            has_contact=fields.contact is not None,
        ),
    )
    return profile.model_copy(
        update={
            "contact": _checked_contact(fields.contact),
            "work_history": fields.work_history,
        }
    )


__all__ = [
    "SUPPORTED_EXTENSIONS",
    "ExtractedResumeProfile",
    "ResumeParsingError",
    "enrich_profile",
    "extract_profile_fields",
    "extract_text",
    "parse_resume",
    "profile_needs_enrichment",
]
