"""Tests for JWT settings resolution from the environment."""

from __future__ import annotations

import pytest

from infrastructure.auth.config import AuthSettings, get_auth_settings

ENV_VARS = ("JWT_SECRET_KEY", "JWT_ALGORITHM", "JWT_EXPIRY_MINUTES")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_get_auth_settings_raises_when_secret_key_unset() -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        get_auth_settings()


def test_get_auth_settings_reads_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    settings = get_auth_settings()
    assert settings.secret_key == "test-secret"


def test_get_auth_settings_defaults_algorithm_and_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    settings = get_auth_settings()
    assert settings.algorithm == "HS256"
    assert settings.expiry_minutes == 10080


def test_get_auth_settings_reads_algorithm_and_expiry_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("JWT_ALGORITHM", "HS512")
    monkeypatch.setenv("JWT_EXPIRY_MINUTES", "60")
    settings = get_auth_settings()
    assert settings.algorithm == "HS512"
    assert settings.expiry_minutes == 60


def test_auth_settings_is_frozen() -> None:
    settings = AuthSettings(secret_key="k")
    with pytest.raises(AttributeError):
        settings.secret_key = "other"  # type: ignore[misc]
