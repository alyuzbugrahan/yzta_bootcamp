"""Slot tracking: turns a stream of detections into one record per physical fig.

Ported from ``VideoProcessorWorker._process_slots`` (video_processor_worker.py:160). Each fig
in view occupies a slot. A slot locks once it has been present long enough, emitting exactly
one :class:`InspectionResult`; it is not re-emitted while it stays in view, and the slot is
only released after the fig has been gone long enough that a new fig arriving in the same
position is genuinely new.

Per-connection state, like the stabiliser. The Qt signal emission of the original
(``inspection_ready.emit``) is replaced by returning the results, which is what makes this
testable without an event loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.gating import Gate
from app.domain.geometry import box_iou
from app.domain.models import Detection, InspectionResult


@dataclass(slots=True)
class _Slot:
    bbox: tuple[float, float, float, float]
    detection: Detection
    present_samples: int = 0
    present_since: float = 0.0
    locked: bool = False
    absent_samples: int = 0
    absent_since: float | None = None

    def present_for(self, now: float) -> float:
        return now - self.present_since

    def absent_for(self, now: float) -> float:
        return 0.0 if self.absent_since is None else now - self.absent_since


@dataclass(slots=True)
class SlotTracker:
    """Debounces detections into one recorded inspection per fig."""

    presence: Gate
    cooldown: Gate
    iou_threshold: float
    _slots: dict[int, _Slot] = field(default_factory=dict)
    _next_id: int = 0

    def process(
        self, detections: list[Detection], now: float, latency_ms: float
    ) -> list[InspectionResult]:
        """Advance every slot by one frame. Returns inspections that locked on this frame."""
        results: list[InspectionResult] = []
        matched: set[int] = set()

        for detection in detections:
            slot_id = self._match(detection)

            if slot_id is None:
                slot_id = self._next_id
                self._next_id += 1
                self._slots[slot_id] = _Slot(
                    bbox=detection.bbox, detection=detection, present_since=now
                )

            slot = self._slots[slot_id]
            slot.bbox = detection.bbox
            slot.detection = detection
            slot.absent_samples = 0
            slot.absent_since = None
            matched.add(slot_id)

            if slot.locked:
                continue

            slot.present_samples += 1

            if self.presence.is_open(slot.present_samples, slot.present_for(now)):
                slot.locked = True
                results.append(
                    InspectionResult(
                        decision=detection.class_name,
                        confidence=round(detection.confidence, 4),
                        detection=detection,
                        latency_ms=round(latency_ms, 1),
                    )
                )

        self._age_unmatched(matched, now)
        return results

    def reset(self) -> None:
        self._slots.clear()
        self._next_id = 0

    @property
    def active_count(self) -> int:
        return len(self._slots)

    @property
    def locked_count(self) -> int:
        return sum(1 for slot in self._slots.values() if slot.locked)

    def _match(self, detection: Detection) -> int | None:
        best_id: int | None = None
        best_iou = self.iou_threshold

        for slot_id, slot in self._slots.items():
            iou = box_iou(detection.bbox, slot.bbox)
            if iou > best_iou:
                best_iou = iou
                best_id = slot_id

        return best_id

    def _age_unmatched(self, matched: set[int], now: float) -> None:
        for slot_id in list(self._slots):
            if slot_id in matched:
                continue

            slot = self._slots[slot_id]

            # An unlocked slot that disappears was never a fig — drop it immediately, as the
            # desktop pipeline did (video_processor_worker.py:221).
            if not slot.locked:
                del self._slots[slot_id]
                continue

            slot.present_samples = 0
            slot.absent_samples += 1
            if slot.absent_since is None:
                slot.absent_since = now

            if self.cooldown.is_open(slot.absent_samples, slot.absent_for(now)):
                del self._slots[slot_id]
