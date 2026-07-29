"""In-process rate limiting.

A token bucket per key. Buckets refill continuously rather than resetting on a fixed boundary,
so a client cannot spend a full allowance at 11:59:59 and another at 12:00:00.

**This is per-process state.** With N replicas the effective limit is N times what is configured
here, and a restart forgets every bucket. That is an accepted trade for now: the deployment is a
single worker per replica (the model is loaded once per process), and the limits below exist to
blunt credential stuffing and frame floods rather than to enforce a billing quota. Moving to
Redis is the change to make when a second replica appears — noted in §7 of the plan.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RateLimit:
    """``capacity`` requests, refilled over ``per_seconds``."""

    capacity: int
    per_seconds: float

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError("capacity must be >= 1")
        if self.per_seconds <= 0:
            raise ValueError("per_seconds must be > 0")

    @property
    def refill_per_second(self) -> float:
        return self.capacity / self.per_seconds


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float


@dataclass(slots=True)
class TokenBucketLimiter:
    """Tracks buckets by key, evicting idle ones so the map cannot grow without bound.

    Unbounded growth matters here: the key is usually a client IP, and an attacker rotating
    source addresses would otherwise turn the limiter itself into the memory exhaustion it is
    supposed to prevent.
    """

    limit: RateLimit
    clock: object = time.monotonic
    max_keys: int = 10_000
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def check(self, key: str) -> tuple[bool, float]:
        """Consume one token. Returns ``(allowed, retry_after_seconds)``."""
        now = self.clock()
        bucket = self._buckets.get(key)

        if bucket is None:
            self._evict_if_needed(now)
            self._buckets[key] = _Bucket(tokens=self.limit.capacity - 1, updated_at=now)
            return True, 0.0

        elapsed = now - bucket.updated_at
        bucket.tokens = min(
            float(self.limit.capacity),
            bucket.tokens + elapsed * self.limit.refill_per_second,
        )
        bucket.updated_at = now

        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True, 0.0

        deficit = 1.0 - bucket.tokens
        return False, round(deficit / self.limit.refill_per_second, 3)

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._buckets.clear()
        else:
            self._buckets.pop(key, None)

    @property
    def tracked_keys(self) -> int:
        return len(self._buckets)

    def _evict_if_needed(self, now: float) -> None:
        if len(self._buckets) < self.max_keys:
            return

        # Drop anything that has had time to refill completely — a full bucket is
        # indistinguishable from one that was never used.
        full_after = self.limit.per_seconds
        stale = [k for k, b in self._buckets.items() if now - b.updated_at >= full_after]

        for key in stale:
            del self._buckets[key]

        if len(self._buckets) >= self.max_keys:
            # Still full of active clients: drop the least recently seen to stay bounded.
            oldest = min(self._buckets, key=lambda k: self._buckets[k].updated_at)
            del self._buckets[oldest]


class ConcurrencyLimiter:
    """Caps simultaneous holders per key. Used for live WebSocket connections per user."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self._limit = limit
        self._held: dict[str, int] = {}

    def acquire(self, key: str) -> bool:
        current = self._held.get(key, 0)
        if current >= self._limit:
            return False
        self._held[key] = current + 1
        return True

    def release(self, key: str) -> None:
        current = self._held.get(key, 0)
        if current <= 1:
            self._held.pop(key, None)
        else:
            self._held[key] = current - 1

    def held(self, key: str) -> int:
        return self._held.get(key, 0)

    @property
    def limit(self) -> int:
        return self._limit
