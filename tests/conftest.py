"""Shared pytest fixtures.

Sets a deterministic `JWT_SECRET_KEY` for the whole test session at import
time (not a fixture — needs to be in place before any test module-level
code, e.g. route/dependency imports that might resolve `AuthSettings` early,
runs) so `infrastructure.auth`-backed code under test never depends on a
real `.env` being loaded or present. Test-only value, never a real secret.
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-only-jwt-secret-do-not-use-in-prod")
