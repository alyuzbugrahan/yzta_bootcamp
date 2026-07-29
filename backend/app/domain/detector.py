"""Stateless inference over a loaded YOLO model.

Ported from ``vision/inference_engine.py``, with the state boundary corrected. The desktop
``YOLOONNXEngine`` held three different kinds of thing at once:

* the loaded model — expensive, and safe to share;
* ``self._tracks`` — per-scan temporal state;
* ``self._conf`` — a user-adjustable threshold, mutated by the UI slider via
  ``set_conf_threshold`` (inference_engine.py:729).

On a desktop that conflation is invisible because there is one user. On a shared server it is
a correctness bug in both directions: one ``Detector`` per connection would load one model per
farmer and exhaust memory, while one shared instance would let farmers overwrite each other's
tracking state and confidence threshold.

So this class holds *only* the model. Tracking lives in :mod:`app.domain.stabilizer` and
:mod:`app.domain.slots`, constructed per connection; ``conf`` and ``iou`` are arguments to
:meth:`Detector.predict`, never instance state.
"""

from __future__ import annotations

import os
from typing import Protocol

import numpy as np

from app.core.logging import get_logger
from app.domain.candidates import (
    CandidateParams,
    box_to_normalized_frame,
    crop_and_square,
    find_fig_candidates,
)
from app.domain.geometry import nms_indices
from app.domain.models import CLASS_NAMES, Detection

log = get_logger(__name__)

try:
    import onnxruntime as ort

    ONNX_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional extra
    ONNX_AVAILABLE = False

try:
    from ultralytics import YOLO as UltralyticsYOLO

    ULTRALYTICS_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional extra
    ULTRALYTICS_AVAILABLE = False


class ModelUnavailableError(RuntimeError):
    """No usable model could be loaded."""


class DetectorProtocol(Protocol):
    """What the pipeline needs. :class:`Detector` and ``DemoDetector`` both satisfy it."""

    @property
    def backend(self) -> str: ...

    @property
    def is_demo(self) -> bool: ...

    def predict(self, frame: np.ndarray, conf: float, iou: float) -> list[Detection]: ...


class Detector:
    """Loads a ``.pt`` or ``.onnx`` model once and runs inference against it.

    Thread-safe for concurrent :meth:`predict` calls: the method keeps no instance state, and
    both backends release the GIL during compute. Callers must still bound concurrency — see
    ``app/infra/model_pool.py``.
    """

    def __init__(
        self,
        model_path: str,
        input_size: int,
        candidate_params: CandidateParams,
    ) -> None:
        self._model_path = model_path
        self._input_size = input_size
        self._params = candidate_params

        self._session = None
        self._pt_model = None
        self._backend: str | None = None
        self._input_name: str | None = None

        self._load()

    # ── Loading ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Try the configured path, then the sibling ``.pt``/``.onnx``.

        Unlike the desktop app (inference_engine.py:102) there is no silent demo fallback
        here. A production server that cannot load its model must say so; whether to
        substitute a demo detector is a deployment decision, made in the provider layer
        against ``FIGION_MODEL__ALLOW_DEMO``.
        """
        if self._try_load(self._model_path):
            return

        base = os.path.splitext(self._model_path)[0]
        for ext in (".pt", ".onnx"):
            alternative = base + ext
            if alternative != self._model_path and self._try_load(alternative):
                return

        raise ModelUnavailableError(
            f"No loadable model at {self._model_path!r} or its .pt/.onnx siblings. "
            f"onnxruntime={'yes' if ONNX_AVAILABLE else 'no'}, "
            f"ultralytics={'yes' if ULTRALYTICS_AVAILABLE else 'no'}."
        )

    def _try_load(self, path: str) -> bool:
        if not os.path.exists(path):
            return False

        ext = os.path.splitext(path)[1].lower()
        if ext == ".pt":
            return self._load_pt(path)
        if ext == ".onnx":
            return self._load_onnx(path)
        return False

    def _load_pt(self, path: str) -> bool:
        if not ULTRALYTICS_AVAILABLE:
            log.warning("ultralytics_missing", path=path)
            return False
        try:
            self._pt_model = UltralyticsYOLO(path)
            self._backend = "pt"
            log.info("model_loaded", backend="pt", path=path)
            return True
        except Exception as exc:  # noqa: BLE001 - ultralytics raises arbitrary types on bad weights
            log.error("pt_load_failed", path=path, error=str(exc))
            return False

    def _load_onnx(self, path: str) -> bool:
        if not ONNX_AVAILABLE:
            log.warning("onnxruntime_missing", path=path)
            return False
        try:
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 4
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            self._session = ort.InferenceSession(
                path, sess_options=opts, providers=["CPUExecutionProvider"]
            )
            self._input_name = self._session.get_inputs()[0].name
            self._backend = "onnx"
            log.info("model_loaded", backend="onnx", path=path)
            return True
        except Exception as exc:  # noqa: BLE001 - onnxruntime raises arbitrary types on bad graphs
            log.error("onnx_load_failed", path=path, error=str(exc))
            return False

    # ── Inference ─────────────────────────────────────────────────────────

    @property
    def backend(self) -> str:
        return self._backend or "unloaded"

    @property
    def is_demo(self) -> bool:
        return False

    @property
    def input_size(self) -> int:
        return self._input_size

    def predict(self, frame: np.ndarray, conf: float, iou: float) -> list[Detection]:
        """Detect figs in one frame. Blocking; run it in a worker thread.

        Returns raw detections with no temporal filtering — that is the stabiliser's job.
        """
        crops = self._build_crops(frame)
        if not crops:
            # No fig-shaped contour: the model is never invoked.
            return []

        frame_h, frame_w = frame.shape[:2]

        # NOTE: the two backends suppress differently, and this asymmetry is inherited from
        # the desktop engine rather than introduced here. The .pt path relies on ultralytics'
        # own per-crop NMS and applies none across crops (inference_engine.py:394), so two
        # overlapping padded candidates can each report the same fig. The .onnx path
        # suppresses per crop and again globally (:454). Unifying them would change detection
        # counts, so it is deliberately left alone until Phase 1's recorded-footage comparison
        # can measure the difference. See docs/WEB_MIGRATION_PLAN.md §6.
        if self._backend == "pt":
            return self._predict_pt(crops, conf, iou, frame_w, frame_h)
        if self._backend == "onnx":
            return self._predict_onnx(crops, conf, iou, frame_w, frame_h)

        raise ModelUnavailableError("Detector has no loaded backend")  # pragma: no cover

    def _build_crops(self, frame: np.ndarray) -> list:
        return [
            crop_and_square(frame, rect, self._input_size)
            for rect in find_fig_candidates(frame, self._params)
        ]

    def _predict_pt(self, crops, conf, iou, frame_w, frame_h) -> list[Detection]:
        try:
            results = self._pt_model.predict(
                [crop.image for crop in crops],
                conf=conf,
                iou=iou,
                imgsz=self._input_size,
                verbose=False,
            )
        # A malformed frame must cost one frame, not the farmer's whole scanning session.
        except Exception as exc:  # noqa: BLE001
            log.error("pt_inference_failed", error=str(exc))
            return []

        detections: list[Detection] = []

        for crop, result in zip(crops, results, strict=False):
            if result.boxes is None or len(result.boxes) == 0:
                continue

            for box in result.boxes:
                class_id = int(box.cls[0])
                detections.append(
                    Detection(
                        class_name=_class_name(class_id),
                        confidence=float(box.conf[0]),
                        bbox=box_to_normalized_frame(
                            box.xyxy[0].tolist(), crop, self._input_size, frame_w, frame_h
                        ),
                    )
                )

        return detections

    def _predict_onnx(self, crops, conf, iou, frame_w, frame_h) -> list[Detection]:
        detections: list[Detection] = []

        for crop in crops:
            try:
                outputs = self._session.run(
                    None, {self._input_name: _preprocess(crop.image, self._input_size)}
                )
            # One bad crop must not discard the other candidates in the same frame.
            except Exception as exc:  # noqa: BLE001
                log.error("onnx_inference_failed", error=str(exc))
                continue

            for class_id, confidence, xyxy in self._decode_onnx(outputs, conf, iou):
                detections.append(
                    Detection(
                        class_name=_class_name(class_id),
                        confidence=confidence,
                        bbox=box_to_normalized_frame(
                            xyxy, crop, self._input_size, frame_w, frame_h
                        ),
                    )
                )

        return self._suppress(detections, iou)

    def _decode_onnx(
        self, outputs: list, conf: float, iou: float
    ) -> list[tuple[int, float, list[float]]]:
        """Convert raw ONNX output into model-space ``(class_id, confidence, xyxy)``."""
        pred = outputs[0]
        if pred.ndim == 3:
            pred = pred[0].T

        items: list[tuple[int, float, list[float]]] = []

        for row in pred:
            scores = row[4:]
            class_id = int(np.argmax(scores))
            confidence = float(scores[class_id])

            if confidence < conf:
                continue

            cx, cy, w, h = row[:4]
            items.append(
                (
                    class_id,
                    confidence,
                    [
                        max(0.0, float(cx - w / 2)),
                        max(0.0, float(cy - h / 2)),
                        min(float(self._input_size), float(cx + w / 2)),
                        min(float(self._input_size), float(cy + h / 2)),
                    ],
                )
            )

        if not items:
            return []

        # Per-crop suppression in model space, matching inference_engine.py:505.
        keep = nms_indices([item[2] for item in items], [item[1] for item in items], iou)
        return [items[i] for i in keep]

    @staticmethod
    def _suppress(detections: list[Detection], iou: float) -> list[Detection]:
        if not detections:
            return []
        keep = nms_indices(
            [d.bbox for d in detections], [d.confidence for d in detections], iou
        )
        return [detections[i] for i in keep]


def _class_name(class_id: int) -> str:
    return CLASS_NAMES[class_id] if 0 <= class_id < len(CLASS_NAMES) else "Unknown"


def _preprocess(image: np.ndarray, input_size: int) -> np.ndarray:
    """BGR uint8 HWC → normalised RGB float32 NCHW."""
    import cv2

    resized = cv2.resize(image, (input_size, input_size))
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    chw = np.transpose(rgb.astype(np.float32) / 255.0, (2, 0, 1))
    return np.expand_dims(chw, axis=0)
