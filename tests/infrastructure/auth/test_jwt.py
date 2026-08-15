"""Tests for JWT create/decode."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt as pyjwt
import pytest

from infrastructure.auth.config import AuthSettings
from infrastructure.auth.errors import AuthError
from infrastructure.auth.jwt import create_access_token, decode_access_token
from shared.errors.codes import ErrorCode
from shared.types.ids import UserId

SETTINGS = AuthSettings(secret_key="test-secret", expiry_minutes=60)


def test_create_and_decode_round_trips_the_user_id() -> None:
    user_id = UserId(uuid4())
    token = create_access_token(user_id, settings=SETTINGS)
    assert decode_access_token(token, settings=SETTINGS) == user_id


def test_decode_rejects_an_expired_token() -> None:
    expired_settings = AuthSettings(secret_key="test-secret", expiry_minutes=-1)
    token = create_access_token(UserId(uuid4()), settings=expired_settings)

    with pytest.raises(AuthError) as exc_info:
        decode_access_token(token, settings=expired_settings)
    assert exc_info.value.code == ErrorCode.UNAUTHORIZED


def test_decode_rejects_a_token_signed_with_a_different_secret() -> None:
    token = create_access_token(UserId(uuid4()), settings=SETTINGS)
    other_settings = AuthSettings(secret_key="a-different-secret", expiry_minutes=60)

    with pytest.raises(AuthError) as exc_info:
        decode_access_token(token, settings=other_settings)
    assert exc_info.value.code == ErrorCode.UNAUTHORIZED


def test_decode_rejects_a_malformed_token() -> None:
    with pytest.raises(AuthError):
        decode_access_token("not-a-jwt-at-all", settings=SETTINGS)


def test_decode_rejects_a_non_uuid_subject_claim() -> None:
    now = datetime.now(UTC)
    token = pyjwt.encode(
        {"sub": "not-a-uuid", "iat": now, "exp": now + timedelta(minutes=5)},
        SETTINGS.secret_key,
        algorithm=SETTINGS.algorithm,
    )

    with pytest.raises(AuthError) as exc_info:
        decode_access_token(token, settings=SETTINGS)
    assert exc_info.value.code == ErrorCode.UNAUTHORIZED
