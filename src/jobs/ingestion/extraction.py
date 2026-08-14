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


_EXTRACTION_PROMPT = """\
Extract the job posting described by the page content below.

Report only what the page states. Do not infer, translate, or normalize the
role into any particular profession or industry vocabulary — this platform
serves every profession, so preserve the posting's own wording for the title,
location, skills, and experience requirement. If a field is not stated on the
page, return null for it rather than guessing.

PAGE CONTENT:
{page_content}
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
    "ExtractedJobFields",
    "PageFetcher",
    "StructuredExtractor",
    "extract_job_from_url",
]
