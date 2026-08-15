"""Tests for the `get_current_user_id` dependency — every protected route
across every service depends on this."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from infrastructure.auth.dependencies import get_current_user_id
from infrastructure.auth.jwt import create_access_token
from shared.types.ids import UserId

ENV_VARS = ("JWT_SECRET_KEY", "JWT_ALGORITHM", "JWT_EXPIRY_MINUTES")


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.mark.asyncio
async def test_resolves_the_user_id_encoded_in_a_valid_token() -> None:
    user_id = UserId(uuid4())
    token = create_access_token(user_id)

    resolved = await get_current_user_id(_bearer(token))

    assert resolved == user_id


@pytest.mark.asyncio
async def test_raises_401_when_no_credentials_are_provided() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_id(None)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_raises_401_for_a_malformed_token() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_id(_bearer("not-a-jwt"))

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "UNAUTHORIZED"
