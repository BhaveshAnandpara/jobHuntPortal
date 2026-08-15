"""FastAPI dependency wiring for Job Ingestion Service's router.

Everything that touches another layer (database, Kafka, the External
Integrations Layer, the LLM Provider Layer) is imported lazily inside these
functions, so merely mounting `jobs.ingestion.api.router` in `api/main.py`
never requires a configured database/broker/LLM — the same pattern
`users/api/dependencies.py` uses.
"""

import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.kafka.producer import EventProducer
from jobs.ingestion.extraction import PageFetcher, StructuredExtractor
from jobs.repository import JobRepository

if TYPE_CHECKING:
    from infrastructure.llm.structured import LLMClient

_EXTRACTION_MODEL = os.environ.get("JOB_EXTRACTION_MODEL", "llama3.2:3b")
"""Job-posting extraction asks a small local model to correctly fill several
fields from a multi-section page in one pass — a harder task than most other
LLM Provider Layer callers ask of `LLMConfig.model`'s process-wide default
(`OLLAMA_MODEL`, `llama3.2:1b` here, sized for this machine's modest
hardware). `LLMCallOptions.model` — an existing per-call override seam
`LLMConfig.merged()` already supports but nothing previously used — lets
this one call site opt into a larger model without moving every other
caller (job matching's scoring, contact ranking, outreach generation) onto
the slower default. Override via `JOB_EXTRACTION_MODEL` if the pulled model
name differs."""


async def get_db_session() -> AsyncIterator[AsyncSession]:
    # Imported lazily — see module docstring.
    from infrastructure.database import get_session

    async for session in get_session():
        yield session


def get_job_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JobRepository:
    return JobRepository(session)


_producer: EventProducer | None = None


def get_event_producer() -> EventProducer:
    """Process-wide `EventProducer`, built once per process.

    `EventProducer` itself is only imported for typing/construction here;
    it does not open a Kafka connection until first published to
    (`confluent_kafka.Producer` is constructed lazily inside it).
    """
    global _producer
    if _producer is None:
        producer_name = "job-ingestion-service"
        _producer = EventProducer(producer_name)
    return _producer


def get_page_fetcher() -> PageFetcher:
    """`PageFetchClient` exposes `fetch_page(url) -> str` as a convenience
    method matching `extraction.py`'s `PageFetcher` protocol exactly (per
    the External Integrations Agent — no adapter needed).
    """
    from infrastructure.external.page_fetch import (
        PageFetchClient,
        PlaywrightPageRenderer,
    )

    return PageFetchClient(PlaywrightPageRenderer())


class _LLMClientExtractor:
    """Bridges the LLM Provider Layer's `LLMClient.complete_structured()`
    (sync) to `extraction.py`'s `StructuredExtractor.extract()` (async)
    protocol — a naming/async-vs-sync adapter only, no LLM transport logic
    of its own. `LLMClient` already owns retries, structured-output repair
    prompting, and error normalization (`infrastructure.llm.structured`).

    Also pins this call to `_EXTRACTION_MODEL` (see its own docstring) via
    `LLMCallOptions`, rather than `LLMConfig.model`'s process-wide default.
    """

    def __init__(self, client: "LLMClient") -> None:
        self._client = client

    async def extract(self, prompt: str, schema: type[BaseModel]) -> BaseModel:
        from infrastructure.llm.config import LLMCallOptions

        return self._client.complete_structured(
            prompt, schema, options=LLMCallOptions(model=_EXTRACTION_MODEL)
        )


def get_structured_extractor() -> StructuredExtractor:
    from infrastructure.llm.structured import LLMClient

    return _LLMClientExtractor(LLMClient())


__all__ = [
    "get_db_session",
    "get_event_producer",
    "get_job_repository",
    "get_page_fetcher",
    "get_structured_extractor",
]
