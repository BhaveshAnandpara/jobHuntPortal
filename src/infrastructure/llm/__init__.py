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

Every user-facing AI action instead resolves the *calling user's* own
provider and API key (docs/llm-provider-config/PRODUCT.md)::

    from infrastructure.llm import LlmNotConfiguredError, get_user_llm_client

    client = await get_user_llm_client(user_id)  # raises LlmNotConfiguredError

A user may also append their own extra instructions to an individual
skill's prompt (docs/prompt-customization/PRODUCT.md). Loading them and
attaching them are both here, and attaching is only ever additive::

    from infrastructure.llm import append_custom_instructions, get_skill_customization

    extra = await get_skill_customization(user_id, SkillKey.RESUME_TAILORING)
    prompt = append_custom_instructions(prompt, extra)  # last, quoted, subordinate

See `infrastructure/llm/user_prompts.py` for why that append point is a
safety boundary rather than a formatting detail.
"""

from __future__ import annotations

from infrastructure.llm.config import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    LLMCallOptions,
    LLMConfig,
)
from infrastructure.llm.errors import (
    LLMFailureReason,
    LlmNotConfiguredError,
    LLMProviderError,
)
from infrastructure.llm.ollama_provider import OllamaProvider
from infrastructure.llm.provider import LLMProvider, LLMRequest, LLMResponse
from infrastructure.llm.structured import LLMClient
from infrastructure.llm.user_client import get_user_llm_client
from infrastructure.llm.user_prompts import (
    append_custom_instructions,
    get_skill_customization,
)

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
    "LlmNotConfiguredError",
    "OllamaProvider",
    "append_custom_instructions",
    "get_skill_customization",
    "get_user_llm_client",
]
