"""Unit tests for infrastructure.external.resilience: RateLimiter and
call_with_resilience, in isolation from any concrete adapter.

Uses a fake clock/sleeper so retry-count and backoff-timing behavior is
verified deterministically and fast, without real delays or real I/O.
"""

import asyncio

import pytest

from infrastructure.external.config import ExternalClientConfig, RetryPolicy
from infrastructure.external.errors import ExternalIntegrationError
from infrastructure.external.resilience import RateLimiter, call_with_resilience


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeSleeper:
    """Records every requested sleep duration instead of actually sleeping.

    Optionally advances a paired fake clock so a RateLimiter under test sees
    time pass as a result of "sleeping".
    """

    def __init__(self, clock: _FakeClock | None = None) -> None:
        self.calls: list[float] = []
        self._clock = clock

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self._clock is not None:
            self._clock.advance(seconds)


class _DummyError(ExternalIntegrationError):
    """Stand-in normalized error type for testing call_with_resilience
    without depending on any concrete adapter's error class.
    """


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limiter_disabled_when_interval_is_zero():
    clock = _FakeClock()
    sleeper = _FakeSleeper(clock)
    limiter = RateLimiter(0.0, clock=clock, sleep=sleeper)

    await limiter.acquire()
    await limiter.acquire()
    await limiter.acquire()

    assert sleeper.calls == []


@pytest.mark.asyncio
async def test_rate_limiter_waits_the_remaining_interval():
    clock = _FakeClock()
    sleeper = _FakeSleeper(clock)
    limiter = RateLimiter(1.0, clock=clock, sleep=sleeper)

    await limiter.acquire()  # first call: nothing to wait for
    await limiter.acquire()  # immediately after: must wait ~1.0s

    assert sleeper.calls == [1.0]


@pytest.mark.asyncio
async def test_rate_limiter_does_not_wait_once_interval_has_elapsed():
    clock = _FakeClock()
    sleeper = _FakeSleeper(clock)
    limiter = RateLimiter(1.0, clock=clock, sleep=sleeper)

    await limiter.acquire()
    clock.advance(2.0)  # more than min_interval_seconds has passed
    await limiter.acquire()

    assert sleeper.calls == []


@pytest.mark.asyncio
async def test_rate_limiter_partial_wait_when_partially_elapsed():
    clock = _FakeClock()
    sleeper = _FakeSleeper(clock)
    limiter = RateLimiter(1.0, clock=clock, sleep=sleeper)

    await limiter.acquire()
    clock.advance(0.4)
    await limiter.acquire()

    assert sleeper.calls == pytest.approx([0.6])


# ---------------------------------------------------------------------------
# call_with_resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_succeeds_on_first_attempt_without_sleeping():
    attempts = {"n": 0}

    async def operation() -> str:
        attempts["n"] += 1
        return "ok"

    sleeper = _FakeSleeper()
    result = await call_with_resilience(
        operation,
        config=ExternalClientConfig(),
        error_type=_DummyError,
        provider="test-provider",
        description="op",
        sleep=sleeper,
    )

    assert result == "ok"
    assert attempts["n"] == 1
    assert sleeper.calls == []


@pytest.mark.asyncio
async def test_retries_transient_failure_then_succeeds():
    attempts = {"n": 0}

    async def operation() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient boom")
        return "ok"

    config = ExternalClientConfig(
        retry=RetryPolicy(
            max_attempts=5,
            initial_backoff_seconds=0.01,
            backoff_multiplier=2.0,
            max_backoff_seconds=1.0,
        )
    )
    sleeper = _FakeSleeper()

    result = await call_with_resilience(
        operation,
        config=config,
        error_type=_DummyError,
        provider="test-provider",
        description="op",
        sleep=sleeper,
    )

    assert result == "ok"
    assert attempts["n"] == 3
    # Slept once between attempt 1->2 and once between 2->3, with
    # exponential backoff applied.
    assert sleeper.calls == pytest.approx([0.01, 0.02])


@pytest.mark.asyncio
async def test_exhausts_retries_and_raises_normalized_error():
    attempts = {"n": 0}

    async def operation() -> str:
        attempts["n"] += 1
        raise RuntimeError("persistent boom")

    config = ExternalClientConfig(
        retry=RetryPolicy(max_attempts=3, initial_backoff_seconds=0.01, backoff_multiplier=1.0)
    )
    sleeper = _FakeSleeper()

    with pytest.raises(_DummyError) as exc_info:
        await call_with_resilience(
            operation,
            config=config,
            error_type=_DummyError,
            provider="test-provider",
            description="risky op",
            sleep=sleeper,
        )

    assert attempts["n"] == 3
    assert len(sleeper.calls) == 2  # one fewer sleep than attempts
    err = exc_info.value
    assert err.provider == "test-provider"
    assert "risky op" in err.message
    assert isinstance(err.cause, RuntimeError)


@pytest.mark.asyncio
async def test_non_retryable_error_is_raised_without_retrying():
    attempts = {"n": 0}

    async def operation() -> str:
        attempts["n"] += 1
        raise _DummyError("not retryable", retryable=False)

    config = ExternalClientConfig(retry=RetryPolicy(max_attempts=5))
    sleeper = _FakeSleeper()

    with pytest.raises(_DummyError):
        await call_with_resilience(
            operation,
            config=config,
            error_type=_DummyError,
            provider="test-provider",
            description="op",
            sleep=sleeper,
        )

    assert attempts["n"] == 1
    assert sleeper.calls == []


@pytest.mark.asyncio
async def test_existing_external_integration_error_is_preserved_not_rewrapped():
    class _OtherError(ExternalIntegrationError):
        pass

    async def operation() -> str:
        raise _OtherError("already normalized", retryable=False)

    with pytest.raises(_OtherError):
        await call_with_resilience(
            operation,
            config=ExternalClientConfig(),
            error_type=_DummyError,
            provider="test-provider",
            description="op",
        )


@pytest.mark.asyncio
async def test_timeout_is_normalized_to_error_type_with_message():
    async def operation() -> str:
        await asyncio.sleep(10)
        return "never"

    config = ExternalClientConfig(
        timeout_seconds=0.02, retry=RetryPolicy(max_attempts=1)
    )

    with pytest.raises(_DummyError) as exc_info:
        await call_with_resilience(
            operation,
            config=config,
            error_type=_DummyError,
            provider="test-provider",
            description="slow fetch",
        )

    assert "timed out" in str(exc_info.value)
    assert "slow fetch" in str(exc_info.value)


@pytest.mark.asyncio
async def test_rate_limiter_is_invoked_once_per_attempt():
    acquisitions = {"n": 0}

    class _CountingLimiter:
        async def acquire(self) -> None:
            acquisitions["n"] += 1

    attempts = {"n": 0}

    async def operation() -> str:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("boom")
        return "ok"

    config = ExternalClientConfig(
        retry=RetryPolicy(max_attempts=3, initial_backoff_seconds=0.001, backoff_multiplier=1.0)
    )

    result = await call_with_resilience(
        operation,
        config=config,
        error_type=_DummyError,
        provider="test-provider",
        description="op",
        rate_limiter=_CountingLimiter(),
        sleep=_FakeSleeper(),
    )

    assert result == "ok"
    assert acquisitions["n"] == 2  # once per attempt, including the retry
