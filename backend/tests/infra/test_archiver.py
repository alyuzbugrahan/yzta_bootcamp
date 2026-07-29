"""Archiving must never apply backpressure to scanning.

The desktop archiver dropped frames when its queue filled rather than blocking the capture
loop (data/image_archiver.py:33). The same choice matters more now, because uploads are network
calls: a farmer whose belt stalls waiting on an S3 retry has lost far more than a JPEG.
"""

from __future__ import annotations

import asyncio

import pytest

from app.infra.archiver import ImageArchiver
from app.infra.storage.local import LocalStorage

JPEG = b"\xff\xd8\xff\xe0fake\xff\xd9"


class SlowStorage:
    """Storage that takes its time, standing in for a struggling S3."""

    def __init__(self, delay: float = 0.2) -> None:
        self._delay = delay
        self.written: list[str] = []

    async def put(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        await asyncio.sleep(self._delay)
        self.written.append(key)


class BrokenStorage:
    def __init__(self) -> None:
        self.attempts = 0

    async def put(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        self.attempts += 1
        raise RuntimeError("bucket is on fire")


@pytest.fixture
def storage(tmp_path) -> LocalStorage:
    return LocalStorage(tmp_path / "archive")


async def test_enqueued_images_are_written(storage):
    archiver = ImageArchiver(storage, workers=2)
    archiver.start()

    for seq in range(5):
        assert archiver.enqueue(f"u1/BATCH_A/fig_{seq:04d}_Healthy.jpg", JPEG) is True

    await archiver.stop()

    assert archiver.counters.written == 5
    assert await storage.exists("u1/BATCH_A/fig_0003_Healthy.jpg") is True


async def test_enqueue_never_blocks(storage):
    """The whole point: offering an image returns immediately whatever storage is doing."""
    archiver = ImageArchiver(SlowStorage(delay=0.5), workers=1)
    archiver.start()

    started = asyncio.get_running_loop().time()
    for seq in range(10):
        archiver.enqueue(f"u1/BATCH_A/fig_{seq:04d}_Healthy.jpg", JPEG)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.05, f"enqueue blocked for {elapsed:.3f}s"

    await archiver.stop(drain_timeout=0.1)


async def test_full_queue_drops_rather_than_blocking(storage):
    """A backlog is bounded. Dropping an image is recoverable; stalling the scan loop is not."""
    archiver = ImageArchiver(SlowStorage(delay=5.0), max_queue=3, workers=1)
    archiver.start()

    accepted = [archiver.enqueue(f"u1/B/fig_{i:04d}_Healthy.jpg", JPEG) for i in range(20)]

    assert accepted.count(False) > 0, "queue grew past its bound"
    assert archiver.counters.dropped > 0

    await archiver.stop(drain_timeout=0.1)


async def test_a_failed_upload_does_not_kill_the_worker(storage):
    """One bad object must not silently stop archiving for the rest of the session."""
    broken = BrokenStorage()
    archiver = ImageArchiver(broken, workers=1)
    archiver.start()

    for seq in range(3):
        archiver.enqueue(f"u1/BATCH_A/fig_{seq:04d}_Healthy.jpg", JPEG)

    await archiver.stop()

    assert broken.attempts == 3, "worker stopped after the first failure"
    assert archiver.counters.failed == 3
    assert archiver.counters.written == 0


async def test_stop_drains_what_is_queued(storage):
    archiver = ImageArchiver(storage, workers=2)
    archiver.start()

    for seq in range(10):
        archiver.enqueue(f"u1/BATCH_A/fig_{seq:04d}_Healthy.jpg", JPEG)

    await archiver.stop()

    assert archiver.counters.written == 10
    assert archiver.pending == 0


async def test_stop_gives_up_on_a_wedged_backend(storage):
    """Shutdown must not hang because storage is unreachable."""
    archiver = ImageArchiver(SlowStorage(delay=30.0), workers=1)
    archiver.start()
    archiver.enqueue("u1/BATCH_A/fig_0001_Healthy.jpg", JPEG)

    started = asyncio.get_running_loop().time()
    await archiver.stop(drain_timeout=0.2)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 2.0, f"shutdown hung for {elapsed:.1f}s"


async def test_workers_run_concurrently(storage):
    """Uploads are network calls, so one slow object must not wedge those behind it."""
    slow = SlowStorage(delay=0.2)
    archiver = ImageArchiver(slow, workers=4)
    archiver.start()

    for seq in range(4):
        archiver.enqueue(f"u1/BATCH_A/fig_{seq:04d}_Healthy.jpg", JPEG)

    started = asyncio.get_running_loop().time()
    await archiver.stop(drain_timeout=3.0)
    elapsed = asyncio.get_running_loop().time() - started

    assert archiver.counters.written == 4
    assert elapsed < 0.6, f"uploads serialised: {elapsed:.2f}s for 4 x 0.2s"
