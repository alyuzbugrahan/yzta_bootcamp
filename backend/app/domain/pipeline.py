"""Per-connection scan pipeline.

Composes the shared detector with this connection's stabiliser and slot tracker. One instance
per WebSocket; the detector it points at is process-wide.

Replaces ``VideoProcessorWorker`` (video_processor_worker.py). Two responsibilities of the
original are deliberately absent:

* The camera read loop — frames now arrive from the client.
* ``_annotate`` — the browser draws the overlay from the normalised boxes this returns, so
  shipping a re-encoded annotated JPEG back would roughly double bandwidth to redraw what the
  client can already draw.
"""

from __future__ import annotations

import time

import numpy as np

from app.domain.detector import DetectorProtocol
from app.domain.gating import Timings
from app.domain.models import FrameOutcome, FrameStats
from app.domain.slots import SlotTracker
from app.domain.stabilizer import TemporalStabilizer


class ScanPipeline:
    """Detect → stabilise → slot, for one scanning session.

    ``clock`` is injected so tests can drive the gates deterministically instead of sleeping.
    It must be monotonic; wall-clock jumps would corrupt the timing gates.
    """

    def __init__(
        self,
        detector: DetectorProtocol,
        timings: Timings,
        clock=time.monotonic,
    ) -> None:
        self._detector = detector
        self._clock = clock

        self._stabilizer = TemporalStabilizer(
            confirm=timings.confirm,
            lost=timings.lost,
            iou_threshold=timings.track_iou_threshold,
        )
        self._slots = SlotTracker(
            presence=timings.presence,
            cooldown=timings.cooldown,
            iou_threshold=timings.slot_iou_threshold,
        )

    @property
    def backend(self) -> str:
        return self._detector.backend

    def reset(self) -> None:
        """Clear temporal state. Used when a session restarts on the same connection."""
        self._stabilizer.reset()
        self._slots.reset()

    def process(self, frame: np.ndarray, conf: float, iou: float) -> FrameOutcome:
        """Run one frame end to end.

        Blocking — the detector call must already be on a worker thread.
        """
        started = self._clock()

        raw = self._detector.predict(frame, conf, iou)
        stable = self._stabilizer.apply(raw, started)

        latency_ms = (self._clock() - started) * 1000.0
        inspections = self._slots.process(stable, started, latency_ms)

        return FrameOutcome(
            detections=stable,
            inspections=inspections,
            stats=FrameStats(
                latency_ms=round(latency_ms, 1),
                detections=len(stable),
                active_slots=self._slots.active_count,
                locked_slots=self._slots.locked_count,
            ),
        )
