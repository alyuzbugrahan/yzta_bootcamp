"""Bounded execution of blocking inference work.

Inference is CPU-bound and blocking. Running it directly in a coroutine would stall the event
loop for every connected farmer for the duration of one prediction, so it is pushed to a worker
thread — both ONNX Runtime and PyTorch release the GIL during compute, which is what makes
threads rather than processes the right tool here.

Concurrency is capped. Without a limit, a hundred farmers each with a frame in flight would
oversubscribe the CPU and every one of them would see latency climb; a limiter converts that
into a short queue and predictable per-frame latency instead.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TypeVar

import anyio
import anyio.to_thread

from app.core.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")


def default_concurrency() -> int:
    return max(1, (os.cpu_count() or 2) - 1)


class InferencePool:
    """Runs blocking callables on threads, bounded by a capacity limiter."""

    def __init__(self, max_concurrent: int = 0) -> None:
        self._limit = max_concurrent if max_concurrent > 0 else default_concurrency()
        self._limiter = anyio.CapacityLimiter(self._limit)
        log.info("inference_pool_ready", max_concurrent=self._limit)

    @property
    def max_concurrent(self) -> int:
        return self._limit

    @property
    def in_flight(self) -> int:
        return self._limiter.borrowed_tokens

    @property
    def waiting(self) -> int:
        return len(self._limiter.statistics().tasks_waiting)

    async def run(self, func: Callable[..., T], *args) -> T:
        """Execute ``func(*args)`` on a worker thread, waiting for a slot if necessary."""
        return await anyio.to_thread.run_sync(func, *args, limiter=self._limiter)
