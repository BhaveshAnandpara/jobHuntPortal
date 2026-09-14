"""Tests for symmetric encryption of secrets stored at rest."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from infrastructure.security.encryption import (
    ENV_VAR,
    EncryptionError,
    EncryptionSettings,
    decrypt,
    encrypt,
    get_encryption_settings,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)


@pytest.fixture
def _key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(ENV_VAR, key)
    return key


def test_get_encryption_settings_raises_when_key_unset() -> None:
    with pytest.raises(RuntimeError, match=ENV_VAR):
        get_encryption_settings()


def test_get_encryption_settings_raises_when_key_is_not_a_fernet_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_VAR, "not-a-fernet-key")
    with pytest.raises(RuntimeError, match="valid Fernet key"):
        get_encryption_settings()


def test_get_encryption_settings_reads_key(_key: str) -> None:
    assert get_encryption_settings().key == _key


def test_encryption_settings_is_frozen() -> None:
    settings = EncryptionSettings(key="k")
    with pytest.raises(AttributeError):
        settings.key = "other"  # type: ignore[misc]


def test_encrypt_decrypt_round_trip_recovers_original(_key: str) -> None:
    plaintext = "sk-test-0123456789-a-real-looking-api-key"
    assert decrypt(encrypt(plaintext)) == plaintext


def test_encrypt_does_not_contain_the_plaintext(_key: str) -> None:
    plaintext = "sk-test-0123456789"
    assert plaintext not in encrypt(plaintext)


def test_encrypt_is_non_deterministic(_key: str) -> None:
    """Fernet embeds a random IV, so the stored value is not a stable
    fingerprint of the secret."""
    plaintext = "sk-test-0123456789"
    assert encrypt(plaintext) != encrypt(plaintext)


def test_round_trip_handles_unicode_and_empty_string(_key: str) -> None:
    for plaintext in ("", "clé-provider-✓"):
        assert decrypt(encrypt(plaintext)) == plaintext


def test_decrypt_raises_on_tampered_ciphertext(_key: str) -> None:
    """A flipped character must fail loudly, not decrypt to garbage.

    The character is flipped in the *middle* of the token, not near its
    end. A Fernet token is base64url, and its trailing characters carry
    padding bits that are not part of any decoded byte: substituting the
    second-to-last character decodes to the *same* bytes whenever the
    original differed only in those non-significant low bits, so the token
    was never actually tampered with and `decrypt` legitimately succeeded.
    That made this test fail intermittently (~6% of runs). Every bit of a
    character in the middle of the token is significant, so the flip below
    always changes the underlying ciphertext and always breaks the HMAC.
    """
    token = encrypt("sk-test-0123456789")
    index = len(token) // 2
    flipped = "A" if token[index] != "A" else "B"
    tampered = token[:index] + flipped + token[index + 1 :]
    assert tampered != token

    with pytest.raises(EncryptionError):
        decrypt(tampered)


def test_decrypt_raises_on_truncated_ciphertext(_key: str) -> None:
    token = encrypt("sk-test-0123456789")

    with pytest.raises(EncryptionError):
        decrypt(token[:-10])


def test_decrypt_raises_on_non_token_input(_key: str) -> None:
    with pytest.raises(EncryptionError):
        decrypt("plainly-not-a-token")


def test_decrypt_raises_when_key_changed(
    _key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rotating the key makes existing ciphertext unreadable — the
    documented operational consequence, asserted rather than assumed."""
    token = encrypt("sk-test-0123456789")
    monkeypatch.setenv(ENV_VAR, Fernet.generate_key().decode())

    with pytest.raises(EncryptionError):
        decrypt(token)


def test_encrypt_raises_when_key_unset() -> None:
    with pytest.raises(RuntimeError, match=ENV_VAR):
        encrypt("sk-test-0123456789")
