"""Domain value objects.

Ported from ``utils/dto.py``. The desktop ``InspectionResult`` carried ``session_id`` and
``batch_id`` because one process owned exactly one session; here those are assigned by the
persistence layer, so the domain object describes only what the vision pipeline knows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

# Model class order. Index 0/1 must match the trained weights.
CLASS_NAMES: tuple[str, ...] = ("Aflatoxin", "Healthy")

DECISION_AFLATOXIN = "Aflatoxin"
DECISION_HEALTHY = "Healthy"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Detection:
    """A single classified region.

    ``bbox`` is ``[x1, y1, x2, y2]`` normalised to 0-1 against the source frame. The desktop
    pipeline already produced normalised coordinates (inference_engine.py:294), which is what
    lets the browser draw the overlay against its own rendered dimensions without knowing the
    resolution the server saw.
    """

    class_name: str
    confidence: float
    bbox: tuple[float, float, float, float]

    @property
    def is_defect(self) -> bool:
        return self.class_name == DECISION_AFLATOXIN


@dataclass(frozen=True, slots=True)
class Rect:
    """An axis-aligned region in absolute source-frame pixels."""

    x: int
    y: int
    w: int
    h: int


@dataclass(slots=True)
class InspectionResult:
    """One fig, recorded once.

    Emitted by :class:`~app.domain.slots.SlotTracker` when a slot locks. ``fig_seq`` is
    filled in by the persistence layer, which allocates it from the database rather than
    from an in-process counter.
    """

    decision: str
    confidence: float
    detection: Detection
    latency_ms: float
    timestamp: datetime = field(default_factory=_utcnow)
    fig_seq: int | None = None
    image_key: str | None = None
    # Database identity, filled in on persist. Present so callers can build the image URL
    # without a second lookup.
    id: int | None = None


@dataclass(frozen=True, slots=True)
class FrameStats:
    """Per-frame telemetry, surfaced to the client so the UI can show honest latency."""

    latency_ms: float
    detections: int
    active_slots: int
    locked_slots: int


@dataclass(frozen=True, slots=True)
class FrameOutcome:
    """Everything one processed frame produced."""

    detections: list[Detection]
    inspections: list[InspectionResult]
    stats: FrameStats
