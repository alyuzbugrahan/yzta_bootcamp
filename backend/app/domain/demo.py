"""Demo detector for environments with no model file.

Ported from ``YOLOONNXEngine._demo_predict`` (inference_engine.py:694) with two fixes:

* The RNG was instance state on the shared engine (``default_rng(42)``, :84). Sharing one
  engine across farmers would interleave their demo streams, so each connection now gets its
  own generator and its own reproducible sequence.
* The ``time.sleep(0.03)`` that faked inference cost is gone. Blocking a pooled worker thread
  to simulate latency starves real work; callers that want to model latency should do it at
  the transport layer.
"""

from __future__ import annotations

import numpy as np

from app.domain.models import Detection


class DemoDetector:
    """Produces plausible detections without a model. Cheap enough to build per connection."""

    def __init__(self, seed: int = 42) -> None:
        self._rng = np.random.default_rng(seed)

    @property
    def backend(self) -> str:
        return "demo"

    @property
    def is_demo(self) -> bool:
        return True

    def predict(self, frame: np.ndarray, conf: float, iou: float) -> list[Detection]:
        roll = float(self._rng.random())

        if roll < 0.33:
            return []

        bbox = (0.20, 0.20, 0.70, 0.70)

        if roll < 0.45:
            return [
                Detection(
                    class_name="Aflatoxin",
                    confidence=float(self._rng.uniform(0.72, 0.97)),
                    bbox=bbox,
                )
            ]

        return [
            Detection(
                class_name="Healthy",
                confidence=float(self._rng.uniform(0.68, 0.97)),
                bbox=bbox,
            )
        ]
