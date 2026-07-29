from __future__ import annotations

import numpy as np
import pytest

from app.domain.gating import Gate, Timings
from app.domain.models import Detection


class FakeClock:
    """Manually advanced monotonic clock, so timing gates are exercised without sleeping."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


class ScriptedDetector:
    """Replays a fixed list of per-frame detections. Stands in for a loaded model."""

    def __init__(self, script: list[list[Detection]]) -> None:
        self._script = script
        self.calls = 0
        self.seen_conf: list[float] = []

    @property
    def backend(self) -> str:
        return "scripted"

    @property
    def is_demo(self) -> bool:
        return False

    def predict(self, frame: np.ndarray, conf: float, iou: float) -> list[Detection]:
        self.seen_conf.append(conf)
        index = min(self.calls, len(self._script) - 1)
        self.calls += 1
        return list(self._script[index])


def detection(
    class_name: str = "Healthy",
    confidence: float = 0.9,
    bbox: tuple[float, float, float, float] = (0.2, 0.2, 0.6, 0.6),
) -> Detection:
    return Detection(class_name=class_name, confidence=confidence, bbox=bbox)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def timings() -> Timings:
    """Desktop-equivalent gates: the constants from config.ini and video_processor_worker.py."""
    return Timings(
        confirm=Gate(min_samples=2, min_seconds=0.07),
        lost=Gate(min_samples=3, min_seconds=0.10),
        presence=Gate(min_samples=3, min_seconds=0.10),
        cooldown=Gate(min_samples=8, min_seconds=0.27),
        track_iou_threshold=0.35,
        slot_iou_threshold=0.25,
    )


@pytest.fixture
def blank_frame() -> np.ndarray:
    return np.zeros((480, 640, 3), dtype=np.uint8)
