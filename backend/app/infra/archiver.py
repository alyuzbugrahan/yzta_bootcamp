"""Background image archiving.

Ported from ``data/image_archiver.py``, which ran a writer thread behind a
``queue.Queue(maxsize=200)`` and dropped frames with a warning when full
(data/image_archiver.py:33). That drop-rather-than-block choice is the important part and is
kept: storage being slow must cost archived images, never scanning. A farmer whose belt stalls
because an S3 call is retrying has lost far more than a JPEG.

One thing the desktop could not get wrong is now possible: uploads are network calls, so a
single stuck request must not wedge the queue behind it. Workers therefore run concurrently and
every upload is individually guarded.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.core.logging import get_logger
from app.infra.storage.base import StorageBackend

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ArchiveJob:
    key: str
    data: bytes


@dataclass(slots=True)
class ArchiveCounters:
    queued: int = 0
    written: int = 0
    dropped: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        # Explicit rather than vars(): slots=True means there is no __dict__ to read.
        return {
            "queued": self.queued,
            "written": self.written,
            "dropped": self.dropped,
            "failed": self.failed,
        }


class ImageArchiver:
    """Bounded, fire-and-forget uploads."""

    def __init__(
        self,
        storage: StorageBackend,
        max_queue: int = 200,
        workers: int = 4,
    ) -> None:
        self._storage = storage
        self._queue: asyncio.Queue[ArchiveJob] = asyncio.Queue(maxsize=max_queue)
        self._worker_count = workers
        self._workers: list[asyncio.Task] = []
        self.counters = ArchiveCounters()

    def start(self) -> None:
        self._workers = [
            asyncio.create_task(self._run(), name=f"archiver-{index}")
            for index in range(self._worker_count)
        ]
        log.info("archiver_started", workers=self._worker_count)

    async def stop(self, drain_timeout: float = 5.0) -> None:
        """Finish what is queued, within reason, then cancel.

        Waiting indefinitely would let a broken storage backend hang shutdown.
        """
        try:
            await asyncio.wait_for(self._queue.join(), timeout=drain_timeout)
        except TimeoutError:
            log.warning("archiver_drain_timeout", pending=self._queue.qsize())

        for worker in self._workers:
            worker.cancel()

        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        log.info("archiver_stopped", **self.counters.as_dict())

    def enqueue(self, key: str, data: bytes) -> bool:
        """Offer an image. Never blocks; returns False if it was dropped.

        ``put_nowait`` rather than ``await put``: awaiting here would apply storage
        backpressure directly to the scan loop, which is the one place it must not reach.
        """
        try:
            self._queue.put_nowait(ArchiveJob(key=key, data=data))
        except asyncio.QueueFull:
            self.counters.dropped += 1
            log.warning("archive_queue_full", key=key, dropped=self.counters.dropped)
            return False

        self.counters.queued += 1
        return True

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._storage.put(job.key, job.data)
                self.counters.written += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a failed upload must not kill the worker
                self.counters.failed += 1
                log.error("archive_failed", key=job.key, error=str(exc))
            finally:
                # In the finally block so that a cancellation during shutdown still balances
                # the get(); otherwise queue.join() in stop() would wait forever.
                self._queue.task_done()
