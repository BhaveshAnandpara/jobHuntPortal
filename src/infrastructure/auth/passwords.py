"""Password hashing — bcrypt directly (not the `passlib` wrapper, which is
increasingly unmaintained).

bcrypt truncates its input at 72 bytes; a password longer than that has its
extra characters silently ignored during hashing. Accepted, documented
limitation for this pass — not worked around (e.g. by pre-hashing with
SHA-256) since it doesn't meaningfully weaken security for realistic
passwords and adds a second scheme to reason about.
"""

import bcrypt


def hash_password(plain: str) -> str:
    """Hash `plain` with a fresh random salt. Returns a `str` suitable for
    storing directly in `UserRecord.password_hash`."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """True if `plain` hashes to `hashed`."""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


__all__ = ["hash_password", "verify_password"]
