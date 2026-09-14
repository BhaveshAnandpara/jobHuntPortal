"""Symmetric encryption for secrets stored at rest, read from the
environment.

Same reasoning as `infrastructure/auth/config.py`'s `JWT_SECRET_KEY`:
`LLM_CONFIG_ENCRYPTION_KEY` is a genuine secret with no safe default —
silently generating or defaulting one would mean every uninitialized
deployment encrypts with a publicly-known (or process-lifetime-only) key.
`get_encryption_settings()` raises a clear error instead of guessing.

Backed by `cryptography.fernet.Fernet` (AES-128-CBC + HMAC-SHA256), which
is authenticated: a tampered or truncated ciphertext fails loudly rather
than decrypting to garbage.

Operational note: rotating `LLM_CONFIG_ENCRYPTION_KEY` makes every value
already encrypted under the old key permanently unreadable. There is no
recovery path after the fact — see docs/llm-provider-config/PRODUCT.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

ENV_VAR = "LLM_CONFIG_ENCRYPTION_KEY"

_GENERATION_HINT = (
    'python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"'
)


class EncryptionError(Exception):
    """Raised when a ciphertext cannot be decrypted.

    Covers a value that was tampered with, truncated, corrupted in
    storage, or encrypted under a different key. Deliberately *not*
    silently swallowed into a `None`/empty-string return: a caller that
    cannot recover the original secret must not proceed as if it could.
    """


@dataclass(frozen=True)
class EncryptionSettings:
    """Resolved encryption settings for the running process."""

    key: str


def get_encryption_settings() -> EncryptionSettings:
    """Read the current process environment into an `EncryptionSettings`.

    Raises `RuntimeError` if `LLM_CONFIG_ENCRYPTION_KEY` is unset or is not
    a valid Fernet key — both must be caught at startup, not on the first
    user who tries to save an API key.
    """
    key = os.environ.get(ENV_VAR)
    if not key:
        raise RuntimeError(
            f"{ENV_VAR} is not set. Generate one (e.g. `{_GENERATION_HINT}`) "
            "and set it in .env."
        )

    try:
        Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"{ENV_VAR} is not a valid Fernet key (expected 32 url-safe "
            f"base64-encoded bytes). Generate one with `{_GENERATION_HINT}`."
        ) from exc

    return EncryptionSettings(key=key)


def _get_fernet() -> Fernet:
    """Build a `Fernet` from the current environment.

    Re-read per call rather than cached at import time so that a process
    which loads `.env` after this module is imported — and tests that
    monkeypatch the variable — see the current value.
    """
    return Fernet(get_encryption_settings().key.encode("utf-8"))


def encrypt(plaintext: str) -> str:
    """Encrypt `plaintext`, returning a storable ASCII token.

    Fernet tokens embed a random IV, so encrypting the same plaintext
    twice yields different ciphertexts — the stored value is not a
    lookup-able fingerprint of the secret.
    """
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Recover the original string from `encrypt`'s output.

    Raises `EncryptionError` if the token is invalid, tampered with, or was
    produced under a different key.
    """
    try:
        return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, TypeError, ValueError) as exc:
        raise EncryptionError(
            "Could not decrypt the stored value — it was tampered with, "
            f"corrupted, or encrypted under a different {ENV_VAR}."
        ) from exc


__all__ = [
    "ENV_VAR",
    "EncryptionError",
    "EncryptionSettings",
    "decrypt",
    "encrypt",
    "get_encryption_settings",
]
