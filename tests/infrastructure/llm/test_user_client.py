"""Tests for `infrastructure.llm.user_client.get_user_llm_client` —
docs/llm-provider-config/TECH.md Task 5.

Runs against a real (SQLite in-memory) async session bound through this
module's own `set_session_factory` seam, so the factory is exercised
end-to-end: a real `user_llm_config` row written by the real repository
(ciphertext at rest), read back, decrypted, and turned into a real
`LLMClient` carrying the right provider implementation.

No vendor SDK ever makes a call here — every provider in this layer opens a
connection only inside `.complete()`, which these tests never invoke.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from infrastructure.llm import user_client
from infrastructure.llm.config import DEFAULT_GEMINI_MODEL, DEFAULT_GROQ_MODEL
from infrastructure.llm.errors import LlmNotConfiguredError
from infrastructure.llm.structured import LLMClient
from infrastructure.llm.user_client import get_user_llm_client
from infrastructure.security.encryption import ENV_VAR
from shared.types.domain.user import User
from shared.types.domain.user_llm_config import LlmProviderName
from shared.types.ids import UserId

API_KEY = "sk-test-abcdefghijklmnop-wxyz"


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """A fresh, test-only Fernet key per test — never a real secret."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(ENV_VAR, key)
    return key


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    """Bind `get_user_llm_client`'s DB access to one isolated SQLite engine.

    Same `set_session_factory` idiom `matching.db`/`contacts.db`/
    `outreach.db` already use for their own consumer-side DB access.
    """
    pytest.importorskip(
        "aiosqlite", reason="async sqlite driver not installed; this test needs it"
    )
    from infrastructure.database import Base
    from users import models  # noqa: F401 — registers the tables

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    user_client.set_session_factory(factory)
    try:
        yield factory
    finally:
        user_client.set_session_factory(None)
        await engine.dispose()


async def _store_config(
    factory: async_sessionmaker,
    *,
    provider: LlmProviderName,
    model: str | None = None,
    api_key: str = API_KEY,
    validated: bool = True,
) -> UserId:
    """Insert a real user + `user_llm_config` row via the real repository."""
    from users.repository import UserLlmConfigRepository, UserRepository

    user_id = UserId(uuid4())
    async with factory() as session:
        await UserRepository(session).add(
            User(
                id=user_id,
                email=f"{user_id}@example.com",
                display_name="Person",
                created_at=datetime.now(UTC),
                timezone="UTC",
            ),
            password_hash="not-a-real-hash",
        )
        await UserLlmConfigRepository(session).upsert(
            user_id=user_id,
            provider=provider,
            model=model,
            api_key=api_key,
            last_validated_at=datetime.now(UTC) if validated else None,
        )
        await session.commit()
    return user_id


# ---------------------------------------------------------------------------
# Not-configured cases — all three mean "add a key in Settings"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raises_when_the_user_has_no_config_row(session_factory) -> None:
    with pytest.raises(LlmNotConfiguredError):
        await get_user_llm_client(UserId(uuid4()))


@pytest.mark.asyncio
async def test_raises_when_the_config_was_never_validated(session_factory) -> None:
    """A saved-but-unvalidated key is treated exactly like no key at all —
    `last_validated_at is None` is the "never proven against the live
    provider" marker (see `UserLlmConfig.last_validated_at`).
    """
    user_id = await _store_config(
        session_factory, provider=LlmProviderName.GROQ, validated=False
    )

    with pytest.raises(LlmNotConfiguredError):
        await get_user_llm_client(user_id)


@pytest.mark.asyncio
async def test_not_configured_error_does_not_subclass_llm_provider_error() -> None:
    """Guards the deliberate taxonomy split: the workflows are full of
    `except LLMProviderError` handlers that degrade to a fallback or a
    retry, and a not-configured-at-all user must never be swallowed by one.
    """
    from infrastructure.llm.errors import LLMProviderError

    assert not issubclass(LlmNotConfiguredError, LLMProviderError)


# ---------------------------------------------------------------------------
# Resolution — provider selection, model defaults, and the real key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "provider_class_name", "expected_model"),
    [
        (LlmProviderName.GROQ, "GroqProvider", DEFAULT_GROQ_MODEL),
        (LlmProviderName.GEMINI, "GeminiProvider", DEFAULT_GEMINI_MODEL),
        (LlmProviderName.CLAUDE, "AnthropicProvider", "claude-sonnet-5"),
        (LlmProviderName.OPENAI, "OpenAIProvider", "gpt-4o-mini"),
    ],
)
async def test_builds_the_right_provider_with_its_default_model(
    session_factory,
    provider: LlmProviderName,
    provider_class_name: str,
    expected_model: str,
) -> None:
    """All four user-selectable providers resolve through the generalized
    `_default_provider` factory, and each gets its own default model when
    the user saved no override — `LLMConfig.model`'s own default is an
    Ollama model name that means nothing to any of them.
    """
    user_id = await _store_config(session_factory, provider=provider)

    client = await get_user_llm_client(user_id)

    assert isinstance(client, LLMClient)
    assert type(client.provider).__name__ == provider_class_name
    assert client.provider.name == provider.value
    assert client._config.model == expected_model


@pytest.mark.asyncio
async def test_uses_the_users_model_override_when_set(session_factory) -> None:
    user_id = await _store_config(
        session_factory, provider=LlmProviderName.GROQ, model="llama-3.1-8b-instant"
    )

    client = await get_user_llm_client(user_id)

    assert client._config.model == "llama-3.1-8b-instant"


@pytest.mark.asyncio
async def test_client_carries_the_users_own_decrypted_key(session_factory) -> None:
    """The whole point of the feature: the call runs on *this* user's key,
    recovered from ciphertext at rest, not on a shared operator key.
    """
    user_id = await _store_config(
        session_factory, provider=LlmProviderName.GROQ, api_key="gsk_user_one_key"
    )

    client = await get_user_llm_client(user_id)

    assert client._config.api_key == "gsk_user_one_key"


@pytest.mark.asyncio
async def test_two_users_get_independently_keyed_clients(session_factory) -> None:
    """One user's configuration never leaks into another's client — the
    regression the replaced module-level singletons made possible.
    """
    first = await _store_config(
        session_factory, provider=LlmProviderName.GROQ, api_key="gsk_first"
    )
    second = await _store_config(
        session_factory, provider=LlmProviderName.OPENAI, api_key="sk_second"
    )

    first_client = await get_user_llm_client(first)
    second_client = await get_user_llm_client(second)

    assert first_client._config.api_key == "gsk_first"
    assert first_client.provider.name == "groq"
    assert second_client._config.api_key == "sk_second"
    assert second_client.provider.name == "openai"
