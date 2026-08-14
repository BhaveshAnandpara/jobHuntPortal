"""LLM Provider Layer — centralized access to LLM inference (Ollama
locally, per about_project.md's technology table). Owned by the LLM
Provider agent (.claude/agents/llm-provider-agent.md). See
docs/architecture/service-boundaries.md#llm-provider-layer.

Internal Python interface only, not an HTTP API — every LLM-calling
component imports this layer directly (an in-process call is the right
tool here; this is a shared *contract* dependency, not a business
dependency). Depended on by: Resume/Profile, Job Ingestion, Job Discovery,
Job Matching, Contact Discovery, and Outreach Services.

Must not: own domain decisions, leak provider-specific types into domain
contracts, or redefine CandidateProfile/JobMatchResult/ContactScore/
Outreach.

Typical usage from a domain component::

    from infrastructure.llm import LLMClient

    client = LLMClient()  # local-first default: OllamaProvider + LLMConfig.from_env()
    result = client.complete_structured(prompt, MyPydanticSchema)

A caller that needs a non-default provider or config injects it explicitly::

    client = LLMClient(provider=some_other_provider, config=my_config)
"""

from __future__ import annotations

from infrastructure.llm.config import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    LLMCallOptions,
    LLMConfig,
)
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from infrastructure.llm.ollama_provider import OllamaProvider
from infrastructure.llm.provider import LLMProvider, LLMRequest, LLMResponse
from infrastructure.llm.structured import LLMClient

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_MODEL",
    "LLMCallOptions",
    "LLMClient",
    "LLMConfig",
    "LLMFailureReason",
    "LLMProvider",
    "LLMProviderError",
    "LLMRequest",
    "LLMResponse",
    "OllamaProvider",
]
