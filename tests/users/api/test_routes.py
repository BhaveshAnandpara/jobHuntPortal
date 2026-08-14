"""API tests for the User Service router — docs/architecture/api-
contracts.md#user-service.

Builds a minimal FastAPI app containing only `users.api.routes.router` and
overrides `get_user_service` with a UserService backed by the fake
in-process repositories from tests/users/conftest.py, so these tests never
require a database. Covers success and error paths (400/404) for all three
endpoints and both ErrorCodes the service can raise.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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


@pytest.fixture
def client(service: UserService) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user_service] = lambda: service
    return TestClient(app)


def _seed_user(users, email: str = "existing@example.com") -> User:
    user = User(
        id=UserId(uuid4()),
        email=email,
        display_name="Existing User",
        created_at=datetime.now(UTC),
        timezone=None,
    )
    users.users[user.id] = user
    return user


# ---------------------------------------------------------------------------
# POST /users
# ---------------------------------------------------------------------------


def test_post_users_success(client: TestClient) -> None:
    response = client.post(
        "/users",
        json={"email": "New@Example.com", "display_name": "New Person", "timezone": "UTC"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert body["display_name"] == "New Person"
    assert "id" in body
    assert "created_at" in body
    assert "timezone" not in body  # UserResponse per api-contracts.md has no timezone field


def test_post_users_invalid_email_returns_400(client: TestClient) -> None:
    response = client.post(
        "/users", json={"email": "not-an-email", "display_name": "Someone"}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_post_users_duplicate_email_returns_400(client: TestClient, users) -> None:
    _seed_user(users, email="taken@example.com")

    response = client.post(
        "/users", json={"email": "taken@example.com", "display_name": "Someone Else"}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_post_users_missing_required_field_returns_422(client: TestClient) -> None:
    response = client.post("/users", json={"email": "user@example.com"})

    assert response.status_code == 422  # FastAPI/pydantic request validation, not UserService


# ---------------------------------------------------------------------------
# GET /users/{user_id}/preferences
# ---------------------------------------------------------------------------


def test_get_preferences_not_found_returns_404(client: TestClient) -> None:
    response = client.get(f"/users/{uuid4()}/preferences")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


def test_get_preferences_found_returns_200(client: TestClient, users) -> None:
    user = _seed_user(users)
    client.put(
        f"/users/{user.id}/preferences",
        json={"target_roles": ["AI Engineer"], "remote_preference": "REMOTE"},
    )

    response = client.get(f"/users/{user.id}/preferences")

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == str(user.id)
    assert body["target_roles"] == ["AI Engineer"]
    assert body["remote_preference"] == "REMOTE"


# ---------------------------------------------------------------------------
# PUT /users/{user_id}/preferences
# ---------------------------------------------------------------------------


def test_put_preferences_user_not_found_returns_404(client: TestClient) -> None:
    response = client.put(
        f"/users/{uuid4()}/preferences", json={"target_roles": ["AI Engineer"]}
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


def test_put_preferences_negative_salary_returns_400(client: TestClient, users) -> None:
    user = _seed_user(users)

    response = client.put(
        f"/users/{user.id}/preferences", json={"min_salary": -1}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_put_preferences_invalid_currency_returns_400(client: TestClient, users) -> None:
    user = _seed_user(users)

    response = client.put(
        f"/users/{user.id}/preferences", json={"salary_currency": "US"}
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_put_preferences_success_creates(client: TestClient, users) -> None:
    user = _seed_user(users)

    response = client.put(
        f"/users/{user.id}/preferences",
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


def test_put_preferences_success_updates_keeping_id(client: TestClient, users) -> None:
    user = _seed_user(users)

    first = client.put(
        f"/users/{user.id}/preferences", json={"target_roles": ["AI Engineer"]}
    ).json()
    second = client.put(
        f"/users/{user.id}/preferences", json={"target_roles": ["Java Backend Engineer"]}
    ).json()

    assert second["id"] == first["id"]
    assert second["target_roles"] == ["Java Backend Engineer"]
