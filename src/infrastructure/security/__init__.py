"""Security infrastructure — reversible encryption for secrets this
platform must be able to read back.

Distinct from `infrastructure/auth/passwords.py`, which is deliberately
one-way (bcrypt): a user's password is only ever *verified*, never
recovered. A user's LLM provider API key is different — the backend has to
present the original value to the provider on every call, so it must be
stored reversibly. This package holds that (and only that) capability.

Like the rest of `infrastructure/`, this is an internal Python interface
with no business knowledge of *what* is being encrypted.
"""

from infrastructure.security.encryption import (
    EncryptionError,
    EncryptionSettings,
    decrypt,
    encrypt,
    get_encryption_settings,
)

__all__ = [
    "EncryptionError",
    "EncryptionSettings",
    "decrypt",
    "encrypt",
    "get_encryption_settings",
]
