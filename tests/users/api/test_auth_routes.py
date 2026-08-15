"""API tests for `POST /auth/login`."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrastructure.auth import hash_password
from shared.types.domain.user import User
from shared.types.ids import UserId
from users.service import UserService

_dependencies_module = pytest.importorskip(
    "users.api.dependencies",
    reason="users.api transitively requires infrastructure.database.Base, not yet exported",
    exc_type=ImportError,
)
_auth_routes_module = pytest.importorskip("users.api.auth_routes", exc_type=ImportError)
get_user_service = _dependencies_module.get_user_service
router = _auth_routes_module.router

DEFAULT_PASSWORD = "correct-password-123"


@pytest.fixture
def client(service: UserService) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user_service] = lambda: service
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


def test_login_success_returns_token_and_user(client: TestClient, users) -> None:
    user = _seed_user(users, email="login@example.com")

    response = client.post(
        "/auth/login", json={"email": "login@example.com", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and body["access_token"]
    assert body["user"]["id"] == str(user.id)
    assert body["user"]["email"] == "login@example.com"


def test_login_unknown_email_returns_401(client: TestClient) -> None:
    response = client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


def test_login_wrong_password_returns_401(client: TestClient, users) -> None:
    _seed_user(users, email="login2@example.com")

    response = client.post(
        "/auth/login", json={"email": "login2@example.com", "password": "wrong-password"}
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


def test_login_returns_a_token_that_decodes_to_the_correct_user_id(
    client: TestClient, users
) -> None:
    from infrastructure.auth.jwt import decode_access_token

    user = _seed_user(users, email="login3@example.com")

    response = client.post(
        "/auth/login", json={"email": "login3@example.com", "password": DEFAULT_PASSWORD}
    )

    token = response.json()["access_token"]
    assert decode_access_token(token) == user.id
