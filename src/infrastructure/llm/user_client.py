"""Per-user `LLMClient` construction — the one place a stored user API key
is decrypted and turned into a usable client.

Every AI-powered action in this system runs on the *calling user's* own
provider credentials rather than one shared, operator-configured key (see
docs/llm-provider-config/PRODUCT.md). `get_user_llm_client(user_id)` is what
makes that true: it loads that user's `user_llm_config` row, refuses to
proceed unless the row exists *and* has been validated live against the
provider, decrypts the key, and hands back an ordinary `LLMClient` — the
same type every call site already consumes, so nothing downstream of this
function knows or cares which provider a given user picked.

Why this module and not `config.py`'s `LLMConfig.from_env()`:
`from_env()` resolves one process-wide provider/model/key from environment
variables and stays in place for operator-level usage. This is the parallel
per-user path; the two share `_default_provider()` (structured.py) so a
provider name means the same thing on both.

Reaching into User Service's repository from the LLM Provider Layer is a
deliberate, spec-sanctioned exception to
docs/architecture/ownership.md#rule-prefer-a-contract-over-reaching-into-internal-state:
`UserLlmConfigRepository.get_decrypted_api_key`'s own docstring names this
function as its single legitimate caller, because a decrypted key must
never travel on a domain object, an API response, or an event payload just
to reach the client factory. The import is lazy (inside the function) so
this module still has no compile-time dependency on User Service.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infrastructure.llm.config import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GROQ_MODEL,
    LLMConfig,
)
from infrastructure.llm.errors import LlmNotConfiguredError
from infrastructure.llm.structured import LLMClient
from infrastructure.logging import format_context, get_logger
from shared.types.domain.user_llm_config import LlmProviderName
from shared.types.ids import UserId

logger = get_logger(__name__)

_session_factory: async_sessionmaker[AsyncSession] | None = None


def set_session_factory(factory: async_sessionmaker[AsyncSession] | None) -> None:
    """Test seam. Pass `None` to restore the process-wide default.

    Mirrors `matching.db`/`contacts.db`/`outreach.db`'s `set_session_factory`
    exactly — this function runs in both request and non-request contexts
    (FastAPI dependencies, LangGraph nodes driven by Kafka consumers), so it
    resolves its own session the same way every other consumer-side DB
    access point in this codebase already does, rather than having a session
    threaded down to it.
    """
    global _session_factory
    _session_factory = factory


@asynccontextmanager
async def _config_session() -> AsyncIterator[AsyncSession]:
    # Imported lazily so importing this module never requires a configured
    # database — the session factory is only resolved on first use.
    from infrastructure.database import session_scope

    async with session_scope(_session_factory) as session:
        yield session


def _default_model_for(provider: LlmProviderName) -> str:
    """The model to call when the user saved no explicit override.

    `LLMConfig.model`'s own default is Ollama's `llama3` placeholder, which
    is meaningless to any of the four hosted providers, and neither
    `GroqProvider` nor `GeminiProvider` substitutes a default of its own
    (both send `LLMRequest.model` verbatim) — so an unset model has to be
    resolved here, per provider, before the config is built.

    The two new providers' constants are imported lazily: reading a default
    model id must not drag the `anthropic`/`openai` SDK into a process that
    selected a different vendor.
    """
    if provider is LlmProviderName.GROQ:
        return DEFAULT_GROQ_MODEL
    if provider is LlmProviderName.GEMINI:
        return DEFAULT_GEMINI_MODEL
    if provider is LlmProviderName.CLAUDE:
        from infrastructure.llm.anthropic_provider import DEFAULT_ANTHROPIC_MODEL

        return DEFAULT_ANTHROPIC_MODEL

    from infrastructure.llm.openai_provider import DEFAULT_OPENAI_MODEL

    return DEFAULT_OPENAI_MODEL


def build_user_llm_config(
    provider: LlmProviderName, model: str | None, api_key: str
) -> LLMConfig:
    """Assemble the `LLMConfig` a stored row maps onto.

    Split out from `get_user_llm_client` so the validation-on-save path
    (docs/llm-provider-config/TECH.md Task 6, which must build a throwaway
    client from a *submitted* key that isn't stored yet) resolves the
    provider/model exactly the way a stored one later will, rather than
    reimplementing the mapping.
    """
    return LLMConfig(
        provider=provider.value,
        model=model or _default_model_for(provider),
        api_key=api_key,
    )


async def get_user_llm_client(user_id: UserId) -> LLMClient:
    """Return an `LLMClient` bound to `user_id`'s own provider and API key.

    Raises `LlmNotConfiguredError` when the user has no config row, has one
    that was never validated against the live provider
    (`last_validated_at is None`), or has one storing no key — all three are
    the same thing to a caller: this user cannot make an LLM call until they
    add a key in Settings.

    The decrypted key lives only on the returned client's `LLMConfig`; it is
    never logged (the log line below carries provider/model only) and never
    placed on a domain object or event payload.
    """
    # Lazy import — see this module's docstring for why the LLM Provider
    # Layer may read User Service's repository at all, and why that must not
    # become a compile-time dependency.
    from users.repository import UserLlmConfigRepository

    async with _config_session() as session:
        repository = UserLlmConfigRepository(session)
        config = await repository.get(user_id)
        if config is None or config.last_validated_at is None:
            raise LlmNotConfiguredError(user_id)
        # Read through the repository rather than off `config`: the masked
        # domain object deliberately cannot carry a usable key.
        api_key = await repository.get_decrypted_api_key(user_id)

    if not api_key:
        raise LlmNotConfiguredError(user_id)

    llm_config = build_user_llm_config(config.provider, config.model, api_key)
    logger.info(
        "Per-user LLM client resolved | %s",
        format_context(
            user_id=user_id, provider=llm_config.provider, model=llm_config.model
        ),
    )
    return LLMClient(config=llm_config)


__all__ = [
    "build_user_llm_config",
    "get_user_llm_client",
    "set_session_factory",
]
