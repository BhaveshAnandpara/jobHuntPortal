"""Centralized timeout, retry, and rate-limit handling for external calls.

Every adapter in this package routes its provider call through
`call_with_resilience` so the policy lives in one place instead of being
re-implemented per client.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from infrastructure.external.config import ExternalClientConfig
from infrastructure.external.errors import ExternalIntegrationError

T = TypeVar("T")

Sleeper = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class RateLimiter:
    """Spaces calls so that consecutive acquisitions are at least
    `min_interval_seconds` apart. A zero interval disables limiting.
    """

    def __init__(
        self,
        min_interval_seconds: float,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last_call: float | None = None

    async def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = self._clock()
            if self._last_call is not None:
                wait = self._min_interval - (now - self._last_call)
                if wait > 0:
                    await self._sleep(wait)
                    now = self._clock()
            self._last_call = now


async def call_with_resilience(
    operation: Callable[[], Awaitable[T]],
    *,
    config: ExternalClientConfig,
    error_type: type[ExternalIntegrationError],
    provider: str,
    description: str,
    rate_limiter: RateLimiter | None = None,
    sleep: Sleeper = asyncio.sleep,
) -> T:
    """Run `operation` under the configured timeout, retry, and rate limit.

    Any provider exception is normalized to `error_type` (and therefore to a
    shared `ErrorCode`). An `ExternalIntegrationError` raised by the
    operation itself is preserved as-is, so a non-retryable failure such as
    `InvalidUrlError` is not retried.
    """
    attempt = 0
    while True:
        attempt += 1
        if rate_limiter is not None:
            await rate_limiter.acquire()
        try:
            return await asyncio.wait_for(operation(), timeout=config.timeout_seconds)
        except asyncio.CancelledError:
            raise
        except ExternalIntegrationError as exc:
            error = exc
        except TimeoutError as exc:
            error = error_type(
                f"{description} timed out after {config.timeout_seconds}s",
                provider=provider,
                cause=exc,
            )
        except Exception as exc:  # noqa: BLE001 - intentional: this is the
            # normalization boundary. Every provider-specific exception
            # (httpx, playwright, vendor SDKs, ...) must be caught here and
            # converted to `error_type` so no raw third-party exception ever
            # escapes this package - see errors.py's module docstring.
            error = error_type(
                f"{description} failed: {exc}",
                provider=provider,
                cause=exc,
            )

        if not error.retryable or attempt >= config.retry.max_attempts:
            raise error
        await sleep(config.retry.backoff_for_attempt(attempt))


__all__ = ["Clock", "RateLimiter", "Sleeper", "call_with_resilience"]
