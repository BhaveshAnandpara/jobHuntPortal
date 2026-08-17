"""Job posting page fetch + extraction for the manual URL path.
See docs/architecture/component-contracts.md#job-ingestion-service.

Page content comes from the External Integrations Layer
(`infrastructure/external/`) and structured fields come from the LLM
Provider Layer (`infrastructure/llm/`). Neither is implemented here: this
service never opens a browser/HTTP connection or calls an LLM SDK
directly (docs/architecture/ownership.md#component--external-toolsapis-it-may-call).
Both are injected as narrow protocols so the *business* decision — which
URL to fetch, what to ask the model for, how to validate what comes back —
stays in this service while the transport stays in infrastructure.

Extraction is deliberately profession-independent: the prompt asks for the
fields `Job` defines (docs/architecture/domain-model.md#job) and nothing
that presumes a profession, industry, or seniority vocabulary.
"""

from typing import Protocol

from pydantic import BaseModel, Field

from jobs.ingestion.errors import JobIngestionError
from shared.errors.codes import ErrorCode

MAX_PAGE_CONTENT_CHARS = 12_000
"""Ceiling on how much fetched page text goes into the extraction prompt.

Some pages (e.g. a careers *listing* page rather than a single posting)
yield hundreds of thousands of characters of visible text — well beyond
what a rate-limited LLM tier can accept in one request. Groq's free
`on_demand` tier caps `openai/gpt-oss-120b` at 8,000 tokens/minute; at
roughly 4 chars/token, 12,000 chars (~3,000 tokens) leaves headroom for
the fixed instructional prompt text and the completion, with enough
budget left in the same minute for `call_with_resilience`'s retries.
A truncated page still carries a job posting's real content up front
(title/company/summary), which is what matters for extraction."""


class ExtractedJobFields(BaseModel):
    """The subset of `Job` an LLM can read off a job posting page.

    Internal to Job Ingestion Service — it never crosses a component
    boundary and is not a second representation of `Job`/`NormalizedJob`.
    It deliberately omits everything the model must not invent: identity
    (`job_id`, `user_id`), provenance (`source_type`, `source_url`) and
    processing state, all of which the ingestion service supplies.
    """

    company: str = Field(description="Hiring organization name")
    title: str = Field(description="Role title exactly as posted")
    location: str | None = Field(
        default=None, description="Work location as posted, or null if absent"
    )
    description: str = Field(description="Full job description text")
    extracted_skills: list[str] = Field(
        default_factory=list,
        description="Skills, tools, or qualifications named in the posting",
    )
    experience_required: str | None = Field(
        default=None,
        description="Experience requirement as free text, e.g. '3-5 years', or null",
    )


class PageFetcher(Protocol):
    """Expected shape of the External Integrations Layer's page-fetch client.

    Assumed interface — reconcile with `infrastructure.external` once the
    External Integrations Agent lands it.
    """

    async def fetch_page(self, url: str) -> str:
        """Return the posting page's text/HTML content, or raise on failure."""


class StructuredExtractor(Protocol):
    """Expected shape of the LLM Provider Layer's structured-output call.

    Assumed interface — reconcile with `infrastructure.llm` once the LLM
    Provider Agent lands it.
    """

    async def extract(self, prompt: str, schema: type[BaseModel]) -> BaseModel:
        """Run `prompt` and parse the response into `schema`."""


_EXTRACTION_PROMPT = _EXTRACTION_PROMPT = """
You are a strict information extraction system.

Your task is to extract structured job-posting data from the page content below.

IMPORTANT RULES:

1. Use ONLY information explicitly present in the page content.
2. Do NOT infer, guess, rewrite, normalize, translate, or complete missing information.
3. If a field is not explicitly supported by the page content, return null.
4. Read the ENTIRE page content before producing the answer.
5. Information may appear anywhere in the page:
   - page title
   - header
   - breadcrumb
   - company/logo text
   - job summary
   - responsibilities
   - requirements
   - qualifications
   - benefits
   - footer metadata
6. Do not assume the main job-description paragraph contains every field.

Extract these fields:

company
- The employer/company/organization offering the job.
- Look especially in the page title, header, logo text, breadcrumb, employer section, or job metadata.
- Do not use another company mentioned only as a customer, partner, client, or technology provider.
- If the employer cannot be determined explicitly, return null.

title
- The exact job title as written on the page.
- Prefer the primary job-posting heading/header.
- Do not rewrite or normalize the title.
- If no explicit job title is present, return null.

location
- The exact location text stated for the job.
- Look in the job header, metadata, location badges, summary, or body.
- Preserve wording such as "Pune, India", "Remote", "Hybrid", or "Bengaluru / Hyderabad".
- Do not infer a location from company headquarters or other unrelated text.
- If no job location is explicitly stated, return null.

description
- A concise extraction of what the role is and what the person will do.
- Use the posting's overview, summary, role description, and responsibilities.
- Do not use the job title alone as the description.
- Do not include unrelated company marketing text unless it directly describes the role.
- If the role itself is not described, return null.

extracted_skills
- Return only skills, technologies, tools, methods, certifications, qualifications, or domain capabilities explicitly required or preferred by the posting.
- Prefer requirements/qualifications/skills sections over responsibilities.
- Do not include:
  - company name
  - job title
  - location
  - generic section headings
  - vague duties such as "work with the team"
- Keep the original wording where practical.
- Return null if no explicit skills or qualifications are stated.

experience_required
- Extract only explicitly stated experience requirements.
- Examples:
  - "2+ years of experience"
  - "3-5 years"
  - "minimum 5 years in mechanical design"
  - "experience with enterprise recruiting"
- Do not infer years of experience from seniority words such as "Senior", "Lead", or "Manager".
- If no explicit experience requirement is stated, return null.

OUTPUT RULES:

- Return ONLY the structured result required by the provided schema.
- Do not add explanations.
- Do not add markdown.
- Do not include evidence or commentary unless the schema explicitly asks for it.
- Use null for every missing field.
- Do not fabricate values to avoid null.

Before finalizing each field, ask internally:
"Can I point to explicit text in the page that supports this value?"
If not, return null.

PAGE CONTENT:
----------------
{page_content}
----------------
"""


async def extract_job_from_url(
    url: str,
    *,
    page_fetcher: PageFetcher,
    extractor: StructuredExtractor,
) -> ExtractedJobFields:
    """Fetch a job posting page and extract its structured fields.

    Raises `JobIngestionError` with `JOB_FETCH_FAILED` if the page cannot be
    retrieved, and with `INVALID_JOB_URL` if the page yields no usable
    posting (empty content, or no company/title — i.e. the URL is reachable
    but is not a job posting).
    """
    try:
        page_content = await page_fetcher.fetch_page(url)
    except Exception as exc:
        raise JobIngestionError(
            ErrorCode.JOB_FETCH_FAILED, f"could not fetch {url}: {exc}"
        ) from exc

    if not page_content or not page_content.strip():
        raise JobIngestionError(
            ErrorCode.INVALID_JOB_URL, f"{url} returned no page content"
        )

    if len(page_content) > MAX_PAGE_CONTENT_CHARS:
        page_content = page_content[:MAX_PAGE_CONTENT_CHARS]

    try:
        extracted = await extractor.extract(
            _EXTRACTION_PROMPT.format(page_content=page_content),
            ExtractedJobFields,
        )
    except Exception as exc:
        raise JobIngestionError(
            ErrorCode.LLM_PROVIDER_ERROR, f"extraction failed for {url}: {exc}"
        ) from exc

    fields = ExtractedJobFields.model_validate(extracted, from_attributes=True)
    if not fields.company.strip() or not fields.title.strip():
        raise JobIngestionError(
            ErrorCode.INVALID_JOB_URL,
            f"{url} does not appear to be a job posting (no company/title found)",
        )
    return fields


__all__ = [
    "MAX_PAGE_CONTENT_CHARS",
    "ExtractedJobFields",
    "PageFetcher",
    "StructuredExtractor",
    "extract_job_from_url",
]
