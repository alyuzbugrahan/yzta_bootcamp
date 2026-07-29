"""Temporal stability filter.

Ported from ``YOLOONNXEngine._apply_temporal_stability`` (inference_engine.py:509). Suppresses
a detection until the same class has been seen in roughly the same place often enough and long
enough, which is what stops the label flickering between Healthy and Aflatoxin on a stationary
fig.

This is per-scan state and is constructed once per WebSocket connection. It used to live on the
shared engine instance, where on a multi-user server one farmer's figs would have advanced
another farmer's track counters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.gating import Gate
from app.domain.geometry import box_iou
from app.domain.models import Detection


@dataclass(slots=True)
class _Track:
    class_name: str
    bbox: tuple[float, float, float, float]
    confidence: float
    hits: int = 1
    first_seen: float = 0.0
    last_seen: float = 0.0
    misses: int = 0
    first_missed: float | None = None

    def age(self, now: float) -> float:
        return now - self.first_seen

    def missing_for(self, now: float) -> float:
        return 0.0 if self.first_missed is None else now - self.first_missed


@dataclass(slots=True)
class TemporalStabilizer:
    """Gates detections on consecutive agreement.

    A track is matched by IoU *and* class: if the model changes its mind about a region, that
    starts a new track rather than reinforcing the old one, so a flip costs the full
    confirmation gate again.
    """

    confirm: Gate
    lost: Gate
    iou_threshold: float
    _tracks: list[_Track] = field(default_factory=list)

    def apply(self, detections: list[Detection], now: float) -> list[Detection]:
        """Return the subset of ``detections`` whose tracks have passed the confirm gate."""
        if not detections:
            self._age_unmatched(set(), now)
            return []

        accepted: list[Detection] = []
        matched: set[int] = set()

        for detection in detections:
            index = self._match(detection)

            if index is None:
                self._tracks.append(
                    _Track(
                        class_name=detection.class_name,
                        bbox=detection.bbox,
                        confidence=detection.confidence,
                        first_seen=now,
                        last_seen=now,
                    )
                )
                continue

            track = self._tracks[index]
            matched.add(index)

            track.bbox = detection.bbox
            track.confidence = detection.confidence
            track.hits += 1
            track.last_seen = now
            track.misses = 0
            track.first_missed = None

            if self.confirm.is_open(track.hits, track.age(now)):
                accepted.append(detection)

        self._age_unmatched(matched, now)
        return accepted

    def reset(self) -> None:
        self._tracks.clear()

    @property
    def track_count(self) -> int:
        return len(self._tracks)

    def _match(self, detection: Detection) -> int | None:
        best_index: int | None = None
        best_iou = 0.0

        for index, track in enumerate(self._tracks):
            if track.class_name != detection.class_name:
                continue

            iou = box_iou(track.bbox, detection.bbox)
            if iou > best_iou:
                best_iou = iou
                best_index = index

        return best_index if best_iou >= self.iou_threshold else None

    def _age_unmatched(self, matched: set[int], now: float) -> None:
        for index, track in enumerate(self._tracks):
            if index in matched:
                continue
            track.misses += 1
            if track.first_missed is None:
                track.first_missed = now

        self._tracks = [
            track
            for track in self._tracks
            if not self.lost.is_open(track.misses, track.missing_for(now))
        ]
