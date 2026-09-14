"""API tests for `GET/PUT /users/me/llm-config` — the per-user AI provider
configuration endpoints (docs/llm-provider-config/TECH.md Task 6).

Same harness as tests/users/api/test_routes.py: a minimal FastAPI app
holding only `users.api.routes.router`, with `get_user_service` overridden
by a `UserService` backed by in-process fakes, and identity supplied by
overriding `get_current_user_id` rather than minting a real JWT. Nothing
here touches a database, an encryption key, or a network.

The one addition is a stubbed `LLMClient`. `PUT` deliberately makes a real
provider call before persisting, so these tests patch
`users.service.LLMClient` — the name the service actually resolves — and
assert on both outcomes: a client that answers (key accepted, config saved)
and a client that raises `LLMProviderError` (key rejected, *nothing*
persisted).
"""

from datetime import UTC, datetime
from typing import ClassVar
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrastructure.auth import hash_password
from infrastructure.auth.dependencies import get_current_user_id
from infrastructure.llm.errors import LLMFailureReason, LLMProviderError
from shared.types.domain.user import User
from shared.types.domain.user_llm_config import LlmProviderName, UserLlmConfig
from shared.types.ids import UserId
from users.service import UserService

# Same guard as test_routes.py: users.api transitively needs
# infrastructure.database.Base, so skip this module rather than failing
# collection for the whole tests/users package if that chain is unavailable.
_dependencies_module = pytest.importorskip(
    "users.api.dependencies",
    reason="users.api transitively requires infrastructure.database.Base",
    exc_type=ImportError,
)
_routes_module = pytest.importorskip("users.api.routes", exc_type=ImportError)
get_user_service = _dependencies_module.get_user_service
router = _routes_module.router

VALID_KEY = "sk-test-000000000000ab12"
"""Long enough that the masked hint is a real tail rather than a bare `...`."""


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeUserLlmConfigRepository:
    """In-process stand-in for `users.repository.UserLlmConfigRepository`.

    Mirrors the real contract that matters to these tests: `get` returns a
    *masked* `UserLlmConfig` (never a plaintext key — the domain type cannot
    carry one), and `upsert` keeps one row per user, replacing it in place.
    The plaintext lives in `self.keys`, which stands in for the encrypted
    column, so a test can assert what was (or was not) stored.
    """

    def __init__(self) -> None:
        self.configs: dict[UserId, UserLlmConfig] = {}
        self.keys: dict[UserId, str] = {}
        self.upsert_calls = 0

    async def get(self, user_id: UserId) -> UserLlmConfig | None:
        return self.configs.get(user_id)

    async def get_decrypted_api_key(self, user_id: UserId) -> str | None:
        return self.keys.get(user_id)

    async def upsert(
        self,
        user_id: UserId,
        provider: LlmProviderName,
        model: str | None,
        api_key: str,
        last_validated_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> UserLlmConfig:
        self.upsert_calls += 1
        existing = self.configs.get(user_id)
        config = UserLlmConfig(
            id=existing.id if existing is not None else uuid4(),
            user_id=user_id,
            provider=provider,
            model=model,
            has_key=bool(api_key),
            key_hint=f"...{api_key[-4:]}" if len(api_key) > 4 else "...",
            last_validated_at=last_validated_at,
            updated_at=updated_at or datetime.now(UTC),
        )
        self.configs[user_id] = config
        self.keys[user_id] = api_key
        return config


class StubLLMClient:
    """Stands in for `LLMClient` during the save-time validation call.

    Records the `LLMConfig` it was handed so a test can prove the submitted
    provider/model/key reached the provider layer, and raises whatever
    `error` is set to so the rejected-key path can be exercised without a
    network.
    """

    error: ClassVar[BaseException | None] = None
    configs: ClassVar[list[object]] = []

    def __init__(self, provider: object = None, config: object = None) -> None:
        self.config = config
        StubLLMClient.configs.append(config)

    def complete_structured(self, prompt: str, schema: type, **kwargs: object) -> object:
        if StubLLMClient.error is not None:
            raise StubLLMClient.error
        return schema(ok="yes")


@pytest.fixture
def llm_configs() -> FakeUserLlmConfigRepository:
    return FakeUserLlmConfigRepository()


@pytest.fixture
def service(users, preferences, llm_configs: FakeUserLlmConfigRepository) -> UserService:
    """Overrides tests/users/conftest.py's two-repository `service` fixture
    with one that also has the LLM config repository wired, matching
    `users.api.dependencies.get_user_service`."""
    return UserService(users=users, preferences=preferences, llm_configs=llm_configs)


@pytest.fixture(autouse=True)
def stub_llm_client(monkeypatch: pytest.MonkeyPatch) -> type[StubLLMClient]:
    """Patch the `LLMClient` name `users.service` resolves, so no test can
    reach a real provider. Accepting the key is the default; a test opts
    into rejection by setting `.error`."""
    StubLLMClient.error = None
    StubLLMClient.configs = []
    monkeypatch.setattr("users.service.LLMClient", StubLLMClient)
    return StubLLMClient


def _rejection() -> LLMProviderError:
    return LLMProviderError(
        LLMFailureReason.PROVIDER_ERROR,
        "groq API error (status_code=401): invalid api key",
        provider="groq",
        model="openai/gpt-oss-120b",
    )


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------


def _authed_client(service: UserService, user_id: UserId) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user_service] = lambda: service
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _anonymous_client(service: UserService) -> TestClient:
    """No `get_current_user_id` override — the real bearer-token dependency
    runs, so a request with no `Authorization` header is a 401."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user_service] = lambda: service
    return TestClient(app)


def _seed_user(users, email: str = "llm-user@example.com") -> User:
    user = User(
        id=UserId(uuid4()),
        email=email,
        display_name="LLM User",
        created_at=datetime.now(UTC),
        timezone=None,
    )
    users.users[user.id] = user
    users.password_hashes[user.id] = hash_password("correct-password-123")
    return user


# ---------------------------------------------------------------------------
# GET /users/me/llm-config
# ---------------------------------------------------------------------------


def test_get_llm_config_unset_returns_404(service: UserService, users) -> None:
    user = _seed_user(users)
    client = _authed_client(service, user.id)

    response = client.get("/users/me/llm-config")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


def test_get_llm_config_requires_auth(service: UserService) -> None:
    response = _anonymous_client(service).get("/users/me/llm-config")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


# ---------------------------------------------------------------------------
# PUT /users/me/llm-config — valid key
# ---------------------------------------------------------------------------


def test_put_llm_config_valid_key_saves_and_returns_masked(
    service: UserService, users, llm_configs: FakeUserLlmConfigRepository
) -> None:
    user = _seed_user(users)
    client = _authed_client(service, user.id)

    response = client.put(
        "/users/me/llm-config",
        json={"provider": "claude", "model": "claude-sonnet-5", "api_key": VALID_KEY},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "claude"
    assert body["model"] == "claude-sonnet-5"
    assert body["key_hint"] == "...ab12"
    assert body["last_validated_at"] is not None
    # The submitted key never comes back, in any field, under any name.
    assert VALID_KEY not in response.text
    assert "api_key" not in body
    assert llm_configs.keys[user.id] == VALID_KEY


def test_put_llm_config_is_retrievable_via_get(service: UserService, users) -> None:
    user = _seed_user(users)
    client = _authed_client(service, user.id)
    client.put(
        "/users/me/llm-config",
        json={"provider": "openai", "model": None, "api_key": VALID_KEY},
    )

    response = client.get("/users/me/llm-config")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "openai"
    assert body["model"] is None
    assert body["key_hint"] == "...ab12"
    assert body["last_validated_at"] is not None
    assert VALID_KEY not in response.text


def test_put_llm_config_validates_with_the_submitted_provider_and_key(
    service: UserService, users, stub_llm_client: type[StubLLMClient]
) -> None:
    """The live call must go out on the configuration the user just typed —
    otherwise "validated" would mean nothing."""
    user = _seed_user(users)
    client = _authed_client(service, user.id)

    client.put(
        "/users/me/llm-config",
        json={"provider": "gemini", "model": "gemini-flash-latest", "api_key": VALID_KEY},
    )

    assert len(stub_llm_client.configs) == 1
    config = stub_llm_client.configs[0]
    assert config.provider == "gemini"
    assert config.model == "gemini-flash-latest"
    assert config.api_key == VALID_KEY


def test_put_llm_config_omitted_model_resolves_a_provider_default(
    service: UserService, users, stub_llm_client: type[StubLLMClient]
) -> None:
    """No override stores None, but the validation call still has to name a
    real model — `build_user_llm_config` supplies the provider's default."""
    user = _seed_user(users)
    client = _authed_client(service, user.id)

    response = client.put(
        "/users/me/llm-config", json={"provider": "groq", "api_key": VALID_KEY}
    )

    assert response.status_code == 200
    assert response.json()["model"] is None
    assert stub_llm_client.configs[0].model  # a concrete Groq model id


def test_put_llm_config_replaces_rather_than_patches(
    service: UserService, users, llm_configs: FakeUserLlmConfigRepository
) -> None:
    """Full-replace semantics, like `PUT /users/me/preferences`: the second
    save's omitted model clears the first save's model instead of keeping
    it, and the row keeps its identity."""
    user = _seed_user(users)
    client = _authed_client(service, user.id)

    first = client.put(
        "/users/me/llm-config",
        json={"provider": "groq", "model": "some-groq-model", "api_key": VALID_KEY},
    ).json()
    second = client.put(
        "/users/me/llm-config",
        json={"provider": "openai", "api_key": "sk-other-key-0000cd34"},
    ).json()

    assert first["model"] == "some-groq-model"
    assert second["provider"] == "openai"
    assert second["model"] is None
    assert second["key_hint"] == "...cd34"
    assert llm_configs.keys[user.id] == "sk-other-key-0000cd34"
    assert len(llm_configs.configs) == 1


# ---------------------------------------------------------------------------
# PUT /users/me/llm-config — rejected key
# ---------------------------------------------------------------------------


def test_put_llm_config_rejected_key_returns_400_and_persists_nothing(
    service: UserService,
    users,
    llm_configs: FakeUserLlmConfigRepository,
    stub_llm_client: type[StubLLMClient],
) -> None:
    user = _seed_user(users)
    client = _authed_client(service, user.id)
    stub_llm_client.error = _rejection()

    response = client.put(
        "/users/me/llm-config",
        json={"provider": "groq", "api_key": "sk-bad-key-000000wxyz"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "VALIDATION_ERROR"
    assert "rejected by Groq" in detail["message"]
    assert llm_configs.upsert_calls == 0
    assert llm_configs.configs == {}

    # Confirmed from the client's side: still nothing configured.
    assert client.get("/users/me/llm-config").status_code == 404


def test_put_llm_config_rejected_key_leaves_an_existing_config_untouched(
    service: UserService,
    users,
    llm_configs: FakeUserLlmConfigRepository,
    stub_llm_client: type[StubLLMClient],
) -> None:
    user = _seed_user(users)
    client = _authed_client(service, user.id)
    client.put("/users/me/llm-config", json={"provider": "groq", "api_key": VALID_KEY})

    stub_llm_client.error = _rejection()
    failed = client.put(
        "/users/me/llm-config",
        json={"provider": "openai", "api_key": "sk-bad-key-000000wxyz"},
    )

    assert failed.status_code == 400
    still_there = client.get("/users/me/llm-config").json()
    assert still_there["provider"] == "groq"
    assert still_there["key_hint"] == "...ab12"
    assert llm_configs.keys[user.id] == VALID_KEY


def test_put_llm_config_rejection_message_never_contains_the_key(
    service: UserService, users, stub_llm_client: type[StubLLMClient]
) -> None:
    user = _seed_user(users)
    client = _authed_client(service, user.id)
    stub_llm_client.error = _rejection()

    response = client.put(
        "/users/me/llm-config", json={"provider": "claude", "api_key": VALID_KEY}
    )

    assert response.status_code == 400
    assert VALID_KEY not in response.text


# ---------------------------------------------------------------------------
# PUT /users/me/llm-config — request validation & auth
# ---------------------------------------------------------------------------


def test_put_llm_config_requires_auth(service: UserService) -> None:
    response = _anonymous_client(service).put(
        "/users/me/llm-config", json={"provider": "groq", "api_key": VALID_KEY}
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


def test_put_llm_config_unknown_provider_returns_422(service: UserService, users) -> None:
    """Ollama is deliberately not a user-selectable provider, so it is
    rejected by the request schema like any other unknown name."""
    user = _seed_user(users)
    client = _authed_client(service, user.id)

    response = client.put(
        "/users/me/llm-config", json={"provider": "ollama", "api_key": VALID_KEY}
    )

    assert response.status_code == 422


def test_put_llm_config_missing_api_key_returns_422(service: UserService, users) -> None:
    user = _seed_user(users)
    client = _authed_client(service, user.id)

    response = client.put("/users/me/llm-config", json={"provider": "groq"})

    assert response.status_code == 422


def test_put_llm_config_blank_api_key_returns_400(
    service: UserService, users, stub_llm_client: type[StubLLMClient]
) -> None:
    user = _seed_user(users)
    client = _authed_client(service, user.id)

    response = client.put(
        "/users/me/llm-config", json={"provider": "groq", "api_key": "   "}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"
    assert stub_llm_client.configs == []  # never worth a provider call


def test_put_llm_config_unknown_user_returns_404(service: UserService) -> None:
    client = _authed_client(service, UserId(uuid4()))

    response = client.put(
        "/users/me/llm-config", json={"provider": "groq", "api_key": VALID_KEY}
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


def test_llm_config_is_scoped_to_the_authenticated_user(service: UserService, users) -> None:
    owner = _seed_user(users, email="owner@example.com")
    other = _seed_user(users, email="other@example.com")

    _authed_client(service, owner.id).put(
        "/users/me/llm-config", json={"provider": "groq", "api_key": VALID_KEY}
    )

    assert _authed_client(service, other.id).get("/users/me/llm-config").status_code == 404
    assert _authed_client(service, owner.id).get("/users/me/llm-config").status_code == 200
