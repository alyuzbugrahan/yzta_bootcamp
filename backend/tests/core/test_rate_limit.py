"""Token bucket and concurrency limiter."""

from __future__ import annotations

import pytest

from app.core.rate_limit import ConcurrencyLimiter, RateLimit, TokenBucketLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def limiter(capacity: int = 3, per_seconds: float = 60.0, clock=None) -> TokenBucketLimiter:
    return TokenBucketLimiter(RateLimit(capacity, per_seconds), clock=clock or FakeClock())


def test_allows_up_to_capacity():
    bucket = limiter(capacity=3)

    assert [bucket.check("ip")[0] for _ in range(3)] == [True, True, True]


def test_blocks_beyond_capacity():
    bucket = limiter(capacity=3)

    for _ in range(3):
        bucket.check("ip")

    allowed, retry_after = bucket.check("ip")

    assert allowed is False
    assert retry_after > 0


def test_keys_are_independent():
    bucket = limiter(capacity=1)

    assert bucket.check("first")[0] is True
    assert bucket.check("second")[0] is True


def test_refills_gradually_rather_than_resetting():
    """A fixed window would let a client spend a full allowance either side of the boundary.

    With continuous refill, waiting a third of the window buys back exactly one token.
    """
    clock = FakeClock()
    bucket = limiter(capacity=3, per_seconds=60.0, clock=clock)

    for _ in range(3):
        bucket.check("ip")
    assert bucket.check("ip")[0] is False

    clock.advance(20.0)
    assert bucket.check("ip")[0] is True
    assert bucket.check("ip")[0] is False


def test_refill_is_capped_at_capacity():
    clock = FakeClock()
    bucket = limiter(capacity=3, per_seconds=60.0, clock=clock)

    bucket.check("ip")
    clock.advance(10_000.0)

    assert [bucket.check("ip")[0] for _ in range(4)] == [True, True, True, False]


def test_retry_after_shrinks_as_the_bucket_refills():
    clock = FakeClock()
    bucket = limiter(capacity=1, per_seconds=10.0, clock=clock)

    bucket.check("ip")
    _, immediately = bucket.check("ip")

    clock.advance(5.0)
    _, later = bucket.check("ip")

    assert later < immediately


def test_idle_keys_are_evicted_so_the_map_stays_bounded():
    """The key is a client IP. An attacker rotating source addresses would otherwise turn the
    limiter into the memory exhaustion it exists to prevent."""
    clock = FakeClock()
    bucket = TokenBucketLimiter(RateLimit(5, 10.0), clock=clock, max_keys=50)

    for index in range(50):
        bucket.check(f"ip-{index}")

    clock.advance(11.0)
    bucket.check("newcomer")

    assert bucket.tracked_keys <= 50


def test_eviction_still_bounds_when_every_key_is_active():
    clock = FakeClock()
    bucket = TokenBucketLimiter(RateLimit(5, 600.0), clock=clock, max_keys=20)

    for index in range(60):
        clock.advance(0.001)
        bucket.check(f"ip-{index}")

    assert bucket.tracked_keys <= 20


def test_reset_clears_a_key():
    bucket = limiter(capacity=1)
    bucket.check("ip")

    bucket.reset("ip")

    assert bucket.check("ip")[0] is True


@pytest.mark.parametrize(("capacity", "per_seconds"), [(0, 60.0), (-1, 60.0), (1, 0.0)])
def test_invalid_limits_are_rejected(capacity, per_seconds):
    with pytest.raises(ValueError):
        RateLimit(capacity, per_seconds)


# ── Concurrency ───────────────────────────────────────────────────────────


def test_concurrency_allows_up_to_the_limit():
    limits = ConcurrencyLimiter(2)

    assert limits.acquire("user") is True
    assert limits.acquire("user") is True
    assert limits.acquire("user") is False


def test_releasing_frees_a_slot():
    limits = ConcurrencyLimiter(1)
    limits.acquire("user")

    limits.release("user")

    assert limits.acquire("user") is True


def test_concurrency_is_per_key():
    limits = ConcurrencyLimiter(1)
    limits.acquire("farmer")

    assert limits.acquire("neighbour") is True


def test_over_release_does_not_go_negative():
    limits = ConcurrencyLimiter(1)

    limits.release("user")
    limits.release("user")

    assert limits.held("user") == 0
    assert limits.acquire("user") is True
