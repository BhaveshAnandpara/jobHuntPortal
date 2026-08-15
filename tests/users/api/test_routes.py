"""API tests for the User Service router — docs/architecture/api-
contracts.md#user-service.

Builds a minimal FastAPI app containing only `users.api.routes.router` and
overrides `get_user_service` with a UserService backed by the fake
in-process repositories from tests/users/conftest.py, so these tests never
require a database. Preferences endpoints derive identity from
`get_current_user_id` (a bearer token in real use) rather than a path
param — overridden directly via `app.dependency_overrides`, the same
pattern already used for `get_user_service`, rather than minting a real
JWT (that's covered separately by tests/infrastructure/auth/).
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrastructure.auth import hash_password
from infrastructure.auth.dependencies import get_current_user_id
from shared.types.domain.user import User
from shared.types.ids import UserId
from users.service import UserService

# users.api.routes/dependencies -> users.repository -> users.models ->
# infrastructure.database.Base. Until the Database Agent's
# infrastructure/database package re-exports `Base` (see this package's
# final report), that chain raises ImportError. Skip this whole module
# gracefully instead of failing collection for the entire tests/users
# package.
_dependencies_module = pytest.importorskip(
    "users.api.dependencies",
    reason="users.api transitively requires infrastructure.database.Base, not yet exported",
    exc_type=ImportError,  # pytest 9.1 defaults to ModuleNotFoundError only; this is a
    # name-not-found ImportError deep in infrastructure.database, still WIP
)
_routes_module = pytest.importorskip("users.api.routes", exc_type=ImportError)
get_user_service = _dependencies_module.get_user_service
router = _routes_module.router

DEFAULT_PASSWORD = "correct-password-123"


@pytest.fixture
def client(service: UserService) -> TestClient:
    """Unauthenticated client — for `POST /users`, which needs no token."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user_service] = lambda: service
    return TestClient(app)


def _authed_client(service: UserService, user_id: UserId) -> TestClient:
    """Client acting as `user_id` — for `/users/me/preferences`, which
    derives identity from the (here, overridden) `get_current_user_id`
    dependency rather than a path param."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user_service] = lambda: service
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _seed_user(users, email: str = "existing@example.com", password: str = DEFAULT_PASSWORD) -> User:
    user = User(
        id=UserId(uuid4()),
        email=email,
        display_name="Existing User",
        created_at=datetime.now(UTC),
        timezone=None,
    )
    users.users[user.id] = user
    users.password_hashes[user.id] = hash_password(password)
    return user


# ---------------------------------------------------------------------------
# POST /users
# ---------------------------------------------------------------------------


def test_post_users_success(client: TestClient) -> None:
    response = client.post(
        "/users",
        json={
            "email": "New@Example.com",
            "display_name": "New Person",
            "password": DEFAULT_PASSWORD,
            "timezone": "UTC",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and body["access_token"]
    user = body["user"]
    assert user["email"] == "new@example.com"
    assert user["display_name"] == "New Person"
    assert "id" in user
    assert "created_at" in user
    assert "timezone" not in user  # UserResponse per api-contracts.md has no timezone field
    assert "password" not in user and "password_hash" not in user


def test_post_users_invalid_email_returns_400(client: TestClient) -> None:
    response = client.post(
        "/users", json={"email": "not-an-email", "display_name": "Someone", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_post_users_duplicate_email_returns_400(client: TestClient, users) -> None:
    _seed_user(users, email="taken@example.com")

    response = client.post(
        "/users",
        json={"email": "taken@example.com", "display_name": "Someone Else", "password": DEFAULT_PASSWORD},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_post_users_short_password_returns_400(client: TestClient) -> None:
    response = client.post(
        "/users", json={"email": "user@example.com", "display_name": "Someone", "password": "short"}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_post_users_missing_required_field_returns_422(client: TestClient) -> None:
    response = client.post("/users", json={"email": "user@example.com"})

    assert response.status_code == 422  # FastAPI/pydantic request validation, not UserService


# ---------------------------------------------------------------------------
# GET /users/me/preferences
# ---------------------------------------------------------------------------


def test_get_preferences_not_found_returns_404(service: UserService) -> None:
    client = _authed_client(service, UserId(uuid4()))

    response = client.get("/users/me/preferences")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


def test_get_preferences_found_returns_200(service: UserService, users) -> None:
    user = _seed_user(users)
    client = _authed_client(service, user.id)
    client.put(
        "/users/me/preferences",
        json={"target_roles": ["AI Engineer"], "remote_preference": "REMOTE"},
    )

    response = client.get("/users/me/preferences")

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == str(user.id)
    assert body["target_roles"] == ["AI Engineer"]
    assert body["remote_preference"] == "REMOTE"


# ---------------------------------------------------------------------------
# PUT /users/me/preferences
# ---------------------------------------------------------------------------


def test_put_preferences_user_not_found_returns_404(service: UserService) -> None:
    client = _authed_client(service, UserId(uuid4()))

    response = client.put("/users/me/preferences", json={"target_roles": ["AI Engineer"]})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


def test_put_preferences_negative_salary_returns_400(service: UserService, users) -> None:
    user = _seed_user(users)
    client = _authed_client(service, user.id)

    response = client.put("/users/me/preferences", json={"min_salary": -1})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_put_preferences_invalid_currency_returns_400(service: UserService, users) -> None:
    user = _seed_user(users)
    client = _authed_client(service, user.id)

    response = client.put("/users/me/preferences", json={"salary_currency": "US"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_put_preferences_success_creates(service: UserService, users) -> None:
    user = _seed_user(users)
    client = _authed_client(service, user.id)

    response = client.put(
        "/users/me/preferences",
        json={
            "target_roles": ["Backend Engineer"],
            "target_locations": ["Remote"],
            "remote_preference": "HYBRID",
            "excluded_companies": ["Acme"],
            "min_salary": 100000,
            "salary_currency": "usd",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["target_roles"] == ["Backend Engineer"]
    assert body["remote_preference"] == "HYBRID"
    assert body["salary_currency"] == "USD"
    assert body["min_salary"] == 100000


def test_put_preferences_success_updates_keeping_id(service: UserService, users) -> None:
    user = _seed_user(users)
    client = _authed_client(service, user.id)

    first = client.put("/users/me/preferences", json={"target_roles": ["AI Engineer"]}).json()
    second = client.put(
        "/users/me/preferences", json={"target_roles": ["Java Backend Engineer"]}
    ).json()

    assert second["id"] == first["id"]
    assert second["target_roles"] == ["Java Backend Engineer"]


def test_preferences_are_scoped_to_the_authenticated_user_not_a_client_supplied_id(
    service: UserService, users
) -> None:
    """The whole point of this retrofit: identity comes from the token
    (here, the override), never from anything the request body/path could
    claim — there is no `user_id` field on these requests at all anymore.
    """
    owner = _seed_user(users, email="owner@example.com")
    other = _seed_user(users, email="other@example.com")
    owner_client = _authed_client(service, owner.id)
    other_client = _authed_client(service, other.id)

    owner_client.put("/users/me/preferences", json={"target_roles": ["Owner Role"]})
    other_client.put("/users/me/preferences", json={"target_roles": ["Other Role"]})

    owner_prefs = owner_client.get("/users/me/preferences").json()
    other_prefs = other_client.get("/users/me/preferences").json()

    assert owner_prefs["user_id"] == str(owner.id)
    assert owner_prefs["target_roles"] == ["Owner Role"]
    assert other_prefs["user_id"] == str(other.id)
    assert other_prefs["target_roles"] == ["Other Role"]
