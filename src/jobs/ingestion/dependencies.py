"""FastAPI dependency wiring for Job Ingestion Service's router.

Everything that touches another layer (database, Kafka, the External
Integrations Layer, the LLM Provider Layer) is imported lazily inside these
functions, so merely mounting `jobs.ingestion.api.router` in `api/main.py`
never requires a configured database/broker/LLM — the same pattern
`users/api/dependencies.py` uses.
"""

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

    Uses `LLMConfig.model`'s process-wide default (one model, set once via
    env) rather than a per-call override — a prior per-call
    `LLMCallOptions.model` pin to a specific Ollama model name broke once
    the configured provider switched to Gemini, since that name isn't a
    valid Gemini model.
    """

    def __init__(self, client: "LLMClient") -> None:
        self._client = client

    async def extract(self, prompt: str, schema: type[BaseModel]) -> BaseModel:
        return self._client.complete_structured(prompt, schema)


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
