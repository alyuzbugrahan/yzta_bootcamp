"""Per-connection scan orchestration.

Replaces ``VideoProcessorWorker``'s run loop (video_processor_worker.py:106). The desktop worker
pulled frames from a camera as fast as it could process them, which self-throttles for free: a
slow frame simply meant the next `read_frame` returned a newer image and the older one was never
seen.

Frames now arrive from the network on someone else's schedule, so that property has to be
recreated deliberately. Each connection holds a **single-slot mailbox**: a frame arriving while
inference is running replaces whatever was waiting rather than joining a queue.

Queueing would be the wrong choice. A farmer sending 15 fps into a pipeline that sustains 8
would build an unbounded backlog, and the boxes drawn on screen would drift further behind the
figs actually under the camera with every second — ending up minutes stale while memory grows.
Dropping means the display stays honest: always the most recent frame the server could handle.
The cost is skipped frames, which is exactly what the sample floors in ``app/domain/gating.py``
are designed to tolerate.
"""

from __future__ import annotations

import asyncio
import time
import uuid as uuid_module
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.domain.models import FrameOutcome, InspectionResult
from app.domain.pipeline import ScanPipeline
from app.infra.model_pool import InferencePool
from app.services.frame_codec import FrameLimits, FrameRejected, decode

log = get_logger(__name__)

# Window used to report an effective frame rate back to the client.
_FPS_WINDOW = 30


@dataclass(slots=True)
class ScanCounters:
    received: int = 0
    processed: int = 0
    dropped: int = 0
    rejected: int = 0
    recorded: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "received": self.received,
            "processed": self.processed,
            "dropped": self.dropped,
            "rejected": self.rejected,
            "recorded": self.recorded,
        }


@dataclass(slots=True)
class ScanConnection:
    """One farmer's live scanning connection.

    Owns the per-connection pipeline state. The shared model lives in the pool.
    """

    session_uuid: uuid_module.UUID
    session_id: int
    user_id: int
    pipeline: ScanPipeline
    pool: InferencePool
    limits: FrameLimits
    batch_id: str
    conf_threshold: float
    iou_threshold: float
    max_fps: float

    on_outcome: Callable[[FrameOutcome], Awaitable[None]] | None = None
    # Receives the inspection and the JPEG bytes that produced it. Passing the original bytes
    # rather than re-encoding the decoded array means archiving costs nothing extra, and the
    # stored image is exactly what the model saw.
    on_inspection: Callable[[InspectionResult, bytes], Awaitable[None]] | None = None
    on_error: Callable[[str, str], Awaitable[None]] | None = None
    on_dropped: Callable[[str], Awaitable[None]] | None = None

    counters: ScanCounters = field(default_factory=ScanCounters)
    paused: bool = False

    _pending: bytes | None = field(default=None, init=False)
    _arrived: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _running: bool = field(default=False, init=False)
    _worker: asyncio.Task | None = field(default=None, init=False)
    _completions: deque[float] = field(
        default_factory=lambda: deque(maxlen=_FPS_WINDOW), init=False
    )
    _last_accepted: float = field(default=0.0, init=False)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        self._running = True
        self._worker = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        self._arrived.set()

        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    # ── Ingest ────────────────────────────────────────────────────────────

    def submit(self, data: bytes) -> str | None:
        """Offer a frame. Returns a drop reason, or ``None`` if the frame was accepted.

        Never blocks and never queues. The caller must relay a drop reason to the client:
        §4.2 tells clients to send the next frame only after the previous one is answered, so
        a silently dropped frame deadlocks a well-behaved client forever. The load harness
        found exactly that — eight frames, then a permanent stall.
        """
        self.counters.received += 1

        if self.paused:
            self.counters.dropped += 1
            return "paused"

        now = time.monotonic()

        # Cheap arrival-rate gate, applied before validation so a flood costs almost nothing.
        min_interval = 1.0 / self.max_fps
        if self._last_accepted and (now - self._last_accepted) < min_interval:
            self.counters.dropped += 1
            return "rate"

        superseded = self._pending is not None
        if superseded:
            # The previous frame was never processed. It is now stale — the newer one
            # describes where the figs actually are.
            self.counters.dropped += 1

        self._last_accepted = now
        self._pending = data
        self._arrived.set()
        return "superseded" if superseded else None

    def set_conf_threshold(self, value: float) -> None:
        self.conf_threshold = value

    @property
    def effective_fps(self) -> float:
        """Frames actually processed per second, measured over a sliding window."""
        if len(self._completions) < 2:
            return 0.0
        span = self._completions[-1] - self._completions[0]
        return round((len(self._completions) - 1) / span, 2) if span > 0 else 0.0

    # ── Worker ────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while self._running:
            await self._arrived.wait()
            self._arrived.clear()

            data = self._pending
            self._pending = None

            if data is None or not self._running:
                continue

            try:
                await self._handle(data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one frame must not kill the connection
                log.error("frame_failed", session=str(self.session_uuid), error=str(exc))
                await self._emit_error("FRAME_FAILED", "Frame could not be processed")

    async def _handle(self, data: bytes) -> None:
        try:
            # Decode and inference both run on the worker thread. Decoding a 1080p JPEG is
            # tens of milliseconds and would otherwise block the event loop.
            outcome = await self.pool.run(self._decode_and_process, data)
        except FrameRejected as exc:
            self.counters.rejected += 1
            await self._emit_error(exc.code, str(exc))
            return

        self.counters.processed += 1
        self._completions.append(time.monotonic())

        if self.on_outcome is not None:
            await self.on_outcome(outcome)

        for inspection in outcome.inspections:
            self.counters.recorded += 1
            if self.on_inspection is not None:
                await self.on_inspection(inspection, data)

    def _decode_and_process(self, data: bytes) -> FrameOutcome:
        """Runs on a worker thread.

        Mutating the pipeline's tracking state off the event loop is safe because at most one
        frame per connection is ever in flight — that is the single-slot mailbox's other job.
        """
        frame = decode(data, self.limits)
        return self.pipeline.process(frame, self.conf_threshold, self.iou_threshold)

    async def _emit_error(self, code: str, message: str) -> None:
        if self.on_error is not None:
            await self.on_error(code, message)


class ConnectionRegistry:
    """Tracks which sessions currently have a live connection.

    A session accepting two simultaneous connections would give each its own tracking state
    while both wrote to the same ``fig_seq`` series — every fig counted twice.
    """

    def __init__(self) -> None:
        self._live: dict[uuid_module.UUID, ScanConnection] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, connection: ScanConnection) -> bool:
        async with self._lock:
            if connection.session_uuid in self._live:
                return False
            self._live[connection.session_uuid] = connection
            return True

    async def release(self, session_uuid: uuid_module.UUID) -> None:
        async with self._lock:
            self._live.pop(session_uuid, None)

    def is_live(self, session_uuid: uuid_module.UUID) -> bool:
        return session_uuid in self._live

    @property
    def count(self) -> int:
        return len(self._live)
