"""Tests for UserLlmConfigRepository against a real (SQLite in-memory)
async SQLAlchemy session — docs/llm-provider-config/TECH.md Task 2.

Uses the `session` fixture from conftest.py, which itself skips (rather
than erroring) when `infrastructure.database.Base` isn't importable or the
`aiosqlite` driver isn't installed — see that fixture's docstring.

The whole point of this repository is that a plaintext API key goes in,
ciphertext is stored, and only a masked hint comes back out — so these
tests assert on all three sides of that, not just the round-trip.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.security.encryption import ENV_VAR
from shared.types.domain.user import User
from shared.types.domain.user_llm_config import LlmProviderName
from shared.types.ids import UserId

_repository_module = pytest.importorskip(
    "users.repository",
    reason="users.models requires infrastructure.database.Base",
    exc_type=ImportError,
)
UserLlmConfigRepository = _repository_module.UserLlmConfigRepository
UserRepository = _repository_module.UserRepository

_models_module = pytest.importorskip(
    "users.models",
    reason="users.models requires infrastructure.database.Base",
    exc_type=ImportError,
)
UserLlmConfigRecord = _models_module.UserLlmConfigRecord

API_KEY = "sk-test-abcdefghijklmnop-wxyz"


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """A fresh, test-only Fernet key per test — never a real secret."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(ENV_VAR, key)
    return key


def _make_user(email: str = "llm@example.com") -> User:
    return User(
        id=UserId(uuid4()),
        email=email,
        display_name="Person",
        created_at=datetime.now(UTC),
        timezone="UTC",
    )


@pytest.mark.asyncio
async def test_get_returns_none_when_absent(session: AsyncSession) -> None:
    repo = UserLlmConfigRepository(session)

    assert await repo.get(UserId(uuid4())) is None


@pytest.mark.asyncio
async def test_get_decrypted_api_key_returns_none_when_absent(
    session: AsyncSession,
) -> None:
    repo = UserLlmConfigRepository(session)

    assert await repo.get_decrypted_api_key(UserId(uuid4())) is None


@pytest.mark.asyncio
async def test_upsert_then_get_returns_masked_config(session: AsyncSession) -> None:
    users_repo = UserRepository(session)
    repo = UserLlmConfigRepository(session)
    user = await users_repo.add(_make_user(), "hashed-password")
    validated_at = datetime.now(UTC)

    created = await repo.upsert(
        user_id=user.id,
        provider=LlmProviderName.CLAUDE,
        model="claude-sonnet-4-5",
        api_key=API_KEY,
        last_validated_at=validated_at,
    )
    fetched = await repo.get(user.id)

    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.user_id == user.id
    assert fetched.provider is LlmProviderName.CLAUDE
    assert fetched.model == "claude-sonnet-4-5"
    assert fetched.has_key is True
    assert fetched.key_hint == "...wxyz"
    assert fetched.last_validated_at is not None
    assert fetched.updated_at is not None


@pytest.mark.asyncio
async def test_domain_object_never_carries_the_plaintext_key(
    session: AsyncSession,
) -> None:
    """Defense in depth: no field, however serialized, may contain a
    usable key."""
    users_repo = UserRepository(session)
    repo = UserLlmConfigRepository(session)
    user = await users_repo.add(_make_user(), "hashed-password")

    config = await repo.upsert(
        user_id=user.id,
        provider=LlmProviderName.OPENAI,
        model=None,
        api_key=API_KEY,
    )

    assert not hasattr(config, "api_key")
    assert API_KEY not in config.model_dump_json()


@pytest.mark.asyncio
async def test_upsert_stores_ciphertext_not_plaintext(session: AsyncSession) -> None:
    users_repo = UserRepository(session)
    repo = UserLlmConfigRepository(session)
    user = await users_repo.add(_make_user(), "hashed-password")

    await repo.upsert(
        user_id=user.id,
        provider=LlmProviderName.GROQ,
        model=None,
        api_key=API_KEY,
    )

    result = await session.execute(
        select(UserLlmConfigRecord).where(UserLlmConfigRecord.user_id == user.id)
    )
    record = result.scalar_one()
    assert record.encrypted_api_key != API_KEY
    assert API_KEY not in record.encrypted_api_key


@pytest.mark.asyncio
async def test_get_decrypted_api_key_round_trips_the_original(
    session: AsyncSession,
) -> None:
    users_repo = UserRepository(session)
    repo = UserLlmConfigRepository(session)
    user = await users_repo.add(_make_user(), "hashed-password")

    await repo.upsert(
        user_id=user.id,
        provider=LlmProviderName.GEMINI,
        model=None,
        api_key=API_KEY,
    )

    assert await repo.get_decrypted_api_key(user.id) == API_KEY


@pytest.mark.asyncio
async def test_upsert_replaces_existing_row(session: AsyncSession) -> None:
    """One active row per user_id — a second upsert must update the
    existing row in place (same id), not insert a second row.
    """
    users_repo = UserRepository(session)
    repo = UserLlmConfigRepository(session)
    user = await users_repo.add(_make_user(), "hashed-password")

    first = await repo.upsert(
        user_id=user.id,
        provider=LlmProviderName.GROQ,
        model="openai/gpt-oss-120b",
        api_key="gsk-first-key-0000",
        last_validated_at=datetime.now(UTC),
    )
    second = await repo.upsert(
        user_id=user.id,
        provider=LlmProviderName.OPENAI,
        model=None,
        api_key="sk-second-key-1111",
    )

    rows = (
        (
            await session.execute(
                select(UserLlmConfigRecord).where(UserLlmConfigRecord.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    fetched = await repo.get(user.id)

    assert len(rows) == 1
    assert second.id == first.id  # existing row's id preserved
    assert fetched is not None
    assert fetched.provider is LlmProviderName.OPENAI
    assert fetched.model is None  # full-replace semantics cleared the override
    assert fetched.key_hint == "...1111"
    assert fetched.last_validated_at is None  # cleared: the new key is unvalidated
    assert await repo.get_decrypted_api_key(user.id) == "sk-second-key-1111"


@pytest.mark.asyncio
async def test_configs_are_isolated_per_user(session: AsyncSession) -> None:
    users_repo = UserRepository(session)
    repo = UserLlmConfigRepository(session)
    first_user = await users_repo.add(_make_user("one@example.com"), "hashed-password")
    second_user = await users_repo.add(_make_user("two@example.com"), "hashed-password")

    await repo.upsert(
        user_id=first_user.id,
        provider=LlmProviderName.CLAUDE,
        model=None,
        api_key="sk-one-aaaa",
    )
    await repo.upsert(
        user_id=second_user.id,
        provider=LlmProviderName.GEMINI,
        model=None,
        api_key="sk-two-bbbb",
    )

    assert await repo.get_decrypted_api_key(first_user.id) == "sk-one-aaaa"
    assert await repo.get_decrypted_api_key(second_user.id) == "sk-two-bbbb"


@pytest.mark.asyncio
async def test_key_hint_hides_everything_for_a_very_short_key(
    session: AsyncSession,
) -> None:
    users_repo = UserRepository(session)
    repo = UserLlmConfigRepository(session)
    user = await users_repo.add(_make_user(), "hashed-password")

    config = await repo.upsert(
        user_id=user.id,
        provider=LlmProviderName.GROQ,
        model=None,
        api_key="abcd",
    )

    assert config.has_key is True
    assert config.key_hint == "..."


@pytest.mark.asyncio
async def test_get_survives_an_undecryptable_row(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rotating LLM_CONFIG_ENCRYPTION_KEY must not make `get` explode —
    the user still needs to see that a config exists so they can replace
    the key. The decrypting path still fails loudly.
    """
    from infrastructure.security.encryption import EncryptionError

    users_repo = UserRepository(session)
    repo = UserLlmConfigRepository(session)
    user = await users_repo.add(_make_user(), "hashed-password")
    await repo.upsert(
        user_id=user.id,
        provider=LlmProviderName.CLAUDE,
        model=None,
        api_key=API_KEY,
    )

    monkeypatch.setenv(ENV_VAR, Fernet.generate_key().decode())

    fetched = await repo.get(user.id)

    assert fetched is not None
    assert fetched.has_key is True
    assert fetched.key_hint is None
    with pytest.raises(EncryptionError):
        await repo.get_decrypted_api_key(user.id)
