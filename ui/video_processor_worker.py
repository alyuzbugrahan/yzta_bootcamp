import time
import cv2
import numpy as np
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal

from vision.camera_manager import CameraManager, CameraNotFoundException
from vision.inference_engine import YOLOONNXEngine
from utils.dto import Detection, InspectionResult
from utils.logger import logger


# ── Tetikleyici ayarları ────────────────────────────────────────────────────
PRESENCE_CONFIRM_FRAMES = 3
COOLDOWN_FRAMES = 8
IOU_MATCH_THRESHOLD = 0.25

# Kutu takip sorunu çözülmüştü, bu yüzden 1 kalmalı.
INFERENCE_EVERY_N = 1
# ────────────────────────────────────────────────────────────────────────────


def _iou(a: list, b: list) -> float:
    xA = max(a[0], b[0])
    yA = max(a[1], b[1])
    xB = min(a[2], b[2])
    yB = min(a[3], b[3])

    inter = max(0.0, xB - xA) * max(0.0, yB - yA)

    if inter == 0:
        return 0.0

    aA = (a[2] - a[0]) * (a[3] - a[1])
    bA = (b[2] - b[0]) * (b[3] - b[1])

    return inter / (aA + bA - inter + 1e-6)


class VideoProcessorWorker(QThread):
    """
    Kamera görüntüsünü okur, YOLO inference çalıştırır,
    bounding box çizilmiş frame'i UI'a gönderir.

    Slot mantığı:
      - Her incir ayrı slot olarak takip edilir.
      - Aynı incir tekrar tekrar kaydedilmez.
      - İncir sahneden çıkınca slot kapanır.
    """

    frame_processed = pyqtSignal(np.ndarray, dict)
    error_occurred = pyqtSignal(str)
    inspection_ready = pyqtSignal(object, np.ndarray)

    def __init__(self, camera: CameraManager, engine: YOLOONNXEngine, parent=None):
        super().__init__(parent)

        self._camera = camera
        self._engine = engine

        self._running = False
        self._scanning = False

        self._last_raw_frame = None
        self._last_detections = []
        self._frame_counter = 0

        self._reset_trigger_state()

    # ── Public API ────────────────────────────────────────────────────────

    def start_pipeline(self):
        self._running = True
        self.start()

    def stop(self):
        self._running = False
        self.wait(3000)

    def set_scanning(self, active: bool):
        self._scanning = active
        self._reset_trigger_state()

    # ── Internal state ────────────────────────────────────────────────────

    def _reset_trigger_state(self):
        # slot_id → {presence, absence, locked, bbox, detection}
        self._slots = {}
        self._next_slot_id = 0

    def _match_slot(self, det: Detection):
        best_iou = IOU_MATCH_THRESHOLD
        best_sid = None

        for sid, slot in self._slots.items():
            score = _iou(det.bbox, slot["bbox"])

            if score > best_iou:
                best_iou = score
                best_sid = sid

        return best_sid

    # ── Main loop ─────────────────────────────────────────────────────────

    def run(self):
        logger.info("VideoProcessorWorker başlatıldı.")

        try:
            self._camera.open_stream()
        except CameraNotFoundException as e:
            self.error_occurred.emit(str(e))
            return

        while self._running:
            t0 = time.perf_counter()

            frame = self._camera.read_frame()

            if frame is None:
                time.sleep(0.05)
                continue

            raw_frame = frame.copy()
            self._frame_counter += 1

            detections = []

            if self._scanning:
                # INFERENCE_EVERY_N = 1 olduğu için her frame güncel detection alır.
                if self._frame_counter % INFERENCE_EVERY_N == 0:
                    detections = self._engine.predict(frame)
                    self._last_detections = detections
                    self._last_raw_frame = raw_frame
                else:
                    detections = []

            annotated = self._annotate(frame, detections)
            latency_ms = (time.perf_counter() - t0) * 1000

            if self._scanning and self._frame_counter % INFERENCE_EVERY_N == 0:
                self._process_slots(detections, latency_ms)

            stats = {
                "latency_ms": round(latency_ms, 1),
                "detections": len(detections),
                "scanning": self._scanning,
                "demo_mode": self._engine.is_demo_mode,
                "active_slots": len(self._slots),
                "locked_slots": sum(1 for s in self._slots.values() if s["locked"]),
            }

            self.frame_processed.emit(annotated, stats)

        self._camera.release()
        logger.info("VideoProcessorWorker durduruldu.")

    # ── Slot yönetimi ─────────────────────────────────────────────────────

    def _process_slots(self, detections: list, latency_ms: float):
        matched = set()

        for det in detections:
            sid = self._match_slot(det)

            if sid is None:
                sid = self._next_slot_id
                self._next_slot_id += 1

                self._slots[sid] = {
                    "presence": 0,
                    "absence": 0,
                    "locked": False,
                    "bbox": det.bbox,
                    "detection": det,
                }

            slot = self._slots[sid]

            slot["bbox"] = det.bbox
            slot["detection"] = det
            slot["absence"] = 0

            matched.add(sid)

            if not slot["locked"]:
                slot["presence"] += 1

                if slot["presence"] >= PRESENCE_CONFIRM_FRAMES:
                    slot["locked"] = True

                    result = self._build_result(det, latency_ms)

                    image_for_save = (
                        self._last_raw_frame
                        if self._last_raw_frame is not None
                        else None
                    )

                    self.inspection_ready.emit(result, image_for_save)

                    logger.debug(
                        f"Slot {sid} tetiklendi: {result.decision} "
                        f"({result.confidence:.0%})"
                    )

        for sid in list(self._slots.keys()):
            if sid in matched:
                continue

            slot = self._slots[sid]
            slot["presence"] = 0

            if slot["locked"]:
                slot["absence"] += 1

                if slot["absence"] >= COOLDOWN_FRAMES:
                    del self._slots[sid]
                    logger.debug(f"Slot {sid} kapandı.")
            else:
                del self._slots[sid]

    # ── Helpers ───────────────────────────────────────────────────────────

    def _build_result(self, det: Detection, latency_ms: float) -> InspectionResult:
        return InspectionResult(
            fig_id=0,
            session_id=0,
            batch_id="",
            decision=det.class_name,
            confidence=round(det.confidence, 4),
            detections=[det],
            timestamp=datetime.now(),
            latency_ms=round(latency_ms, 1),
        )

    def _annotate(self, frame: np.ndarray, detections: list) -> np.ndarray:
        out = frame.copy()
        h, w = out.shape[:2]

        for det in detections:
            x1 = int(det.bbox[0] * w)
            y1 = int(det.bbox[1] * h)
            x2 = int(det.bbox[2] * w)
            y2 = int(det.bbox[3] * h)

            color = (50, 50, 220) if det.class_name == "Aflatoxin" else (50, 200, 80)
            label = f"{det.class_name} {det.confidence:.0%}"

            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)

            (tw, th), _ = cv2.getTextSize(
                label,
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                1,
            )

            cv2.rectangle(
                out,
                (x1, y1 - th - 8),
                (x1 + tw + 4, y1),
                color,
                -1,
            )

            cv2.putText(
                out,
                label,
                (x1 + 2, y1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

        if self._scanning:
            locked = sum(1 for s in self._slots.values() if s["locked"])
            total = len(self._slots)

            if total > 0:
                status_text = f"KILITLI: {locked}/{total}"
                status_color = (50, 200, 80) if locked == total else (0, 180, 220)
            elif len(detections) > 0:
                status_text = "DOGRULANIYOR..."
                status_color = (0, 180, 220)
            else:
                status_text = "BEKLENIYOR..."
                status_color = (100, 100, 100)

            cv2.putText(
                out,
                status_text,
                (10, h - 14),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                status_color,
                1,
                cv2.LINE_AA,
            )

        return out