"""Transport-level configuration shared by every external adapter.

Timeouts, retry policy, and rate limiting are configured here once rather
than per adapter — see
docs/architecture/service-boundaries.md#external-integrations-layer.
"""

import os
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_backoff_seconds: float = 0.5
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 8.0

    def backoff_for_attempt(self, attempt: int) -> float:
        """Backoff before the retry that follows `attempt` (1-based)."""
        delay = self.initial_backoff_seconds * (self.backoff_multiplier ** (attempt - 1))
        return min(delay, self.max_backoff_seconds)


@dataclass(frozen=True)
class ExternalClientConfig:
    timeout_seconds: float = 30.0
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    min_interval_seconds: float = 0.0
    """Minimum spacing between calls made through one client instance. 0 disables rate limiting."""

    @classmethod
    def from_env(cls, prefix: str = "EXTERNAL_") -> "ExternalClientConfig":
        base = cls()
        return replace(
            base,
            timeout_seconds=_env_float(
                f"{prefix}TIMEOUT_SECONDS", base.timeout_seconds
            ),
            min_interval_seconds=_env_float(
                f"{prefix}MIN_INTERVAL_SECONDS", base.min_interval_seconds
            ),
            retry=replace(
                base.retry,
                max_attempts=int(
                    _env_float(f"{prefix}MAX_ATTEMPTS", base.retry.max_attempts)
                ),
            ),
        )


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


__all__ = ["ExternalClientConfig", "RetryPolicy"]
