import os
import time
import cv2
import numpy as np
from typing import List, Dict, Optional
from utils.dto import Detection
from utils.config_manager import ConfigManager
from utils.logger import logger

try:
    import onnxruntime as ort
    ONNX_AVAILABLE = True
except ImportError:
    ONNX_AVAILABLE = False

try:
    from ultralytics import YOLO as UltralyticsYOLO
    ULTRALYTICS_AVAILABLE = True
except ImportError:
    ULTRALYTICS_AVAILABLE = False

CLASS_NAMES = ["Aflatoxin", "Healthy"]


class YOLOONNXEngine:
    """
    Hem .pt (Ultralytics) hem .onnx (onnxruntime) modelini destekler.

    Ana mantık:
      1. Önce frame içinde incir adayı var mı diye bakar.
      2. İncir adayı yoksa YOLO hiç çalışmaz, boş liste döner.
      3. İncir adaylarını crop'lar, kare yapar ve modele gönderir.
      4. Model sonucunu orijinal frame koordinatlarına geri çevirir.
      5. Aynı bölgede aynı class 3 frame üst üste görülmeden sonucu dışarı vermez.

    Böylece:
      - İncir yokken rastgele Healthy/Aflatoxin çizme azalır.
      - Sabit incirde class yazısının frame frame zıplaması azalır.
    """

    def __init__(
        self,
        model_path: str = None,
        conf_threshold: float = None,
        iou_threshold: float = None,
    ):
        cfg = ConfigManager()

        configured = cfg.get_path("model", "model_path", "models/final_model.onnx")
        self._model_path = model_path or configured

        self._conf = (
            conf_threshold
            if conf_threshold is not None
            else cfg.get_float("model", "conf_threshold", 0.50)
        )

        self._iou = (
            iou_threshold
            if iou_threshold is not None
            else cfg.get_float("model", "iou_threshold", 0.45)
        )

        self._input_size = cfg.get_int("model", "input_size", 640)

        # İncir adayı filtreleri
        self._min_candidate_area_px = cfg.get_int("vision", "min_candidate_area_px", 2500)
        self._min_candidate_area_ratio = cfg.get_float("vision", "min_candidate_area_ratio", 0.006)
        self._max_candidate_area_ratio = cfg.get_float("vision", "max_candidate_area_ratio", 0.80)
        self._candidate_padding_ratio = cfg.get_float("vision", "candidate_padding_ratio", 0.08)

        # 3 frame stability ayarları
        self._stability_required = cfg.get_int("vision", "stability_required", 2)
        self._stable_iou_threshold = cfg.get_float("vision", "stable_iou_threshold", 0.35)
        self._max_missing_frames = cfg.get_int("vision", "max_missing_frames", 3)

        self._tracks: List[Dict] = []

        self._session = None
        self._pt_model = None
        self._demo_mode = False
        self._backend = None
        self._input_name = None
        self._rng = np.random.default_rng(42)

        self._load_model()

    # ── Model yükleme ─────────────────────────────────────────────────────

    def _load_model(self):
        if self._try_load(self._model_path):
            return

        base = os.path.splitext(self._model_path)[0]

        for alt_ext in (".pt", ".onnx"):
            alt = base + alt_ext

            if alt != self._model_path and self._try_load(alt):
                return

        self._demo_mode = True
        self._backend = "demo"
        logger.info("Model bulunamadı → Demo modu aktif.")

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
            logger.warning(f"ultralytics kurulu değil, {path} yüklenemedi.")
            return False

        try:
            self._pt_model = UltralyticsYOLO(path)
            self._backend = "pt"
            logger.info(f"Ultralytics .pt modeli yüklendi: {path}")
            return True

        except Exception as e:
            logger.error(f".pt yüklenemedi: {e}")
            return False

    def _load_onnx(self, path: str) -> bool:
        if not ONNX_AVAILABLE:
            logger.warning("onnxruntime kurulu değil.")
            return False

        try:
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 4
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            self._session = ort.InferenceSession(
                path,
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )

            self._input_name = self._session.get_inputs()[0].name
            self._backend = "onnx"
            logger.info(f"ONNX modeli yüklendi: {path}")
            return True

        except Exception as e:
            logger.error(f"ONNX yüklenemedi: {e}")
            return False

    # ── OpenCV: frame içindeki incir adaylarını bul ───────────────────────

    def _find_figs(self, frame: np.ndarray):
        """
        Frame içinde incir adayı olabilecek konturları döndürür.
        HSV kullanılmaz.

        Dönüş:
          [(x, y, w, h), ...]
        """
        frame_h, frame_w = frame.shape[:2]
        frame_area = frame_w * frame_h

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)

        _, thresh = cv2.threshold(
            blurred,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )

        kernel = np.ones((5, 5), np.uint8)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

        contours, _ = cv2.findContours(
            thresh,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )

        fig_rects = []

        for cnt in contours:
            area = cv2.contourArea(cnt)

            if area < self._min_candidate_area_px:
                continue

            area_ratio = area / frame_area

            if area_ratio < self._min_candidate_area_ratio:
                continue

            if area_ratio > self._max_candidate_area_ratio:
                continue

            x, y, w, h = cv2.boundingRect(cnt)

            if w <= 0 or h <= 0:
                continue

            aspect_ratio = w / float(h)

            # Çok ince/uzun nesneleri ele.
            if aspect_ratio < 0.35 or aspect_ratio > 2.85:
                continue

            rect_area = w * h
            fill_ratio = area / float(rect_area + 1e-6)

            # Kablo, kenar, parça parça gürültü gibi nesneleri ele.
            if fill_ratio < 0.25:
                continue

            pad = int(max(w, h) * self._candidate_padding_ratio)

            x1 = max(0, x - pad)
            y1 = max(0, y - pad)
            x2 = min(frame_w, x + w + pad)
            y2 = min(frame_h, y + h + pad)

            fig_rects.append((x1, y1, x2 - x1, y2 - y1))

        return fig_rects

    def _crop_and_square(self, frame: np.ndarray, x: int, y: int, w: int, h: int):
        """
        Aday bölgeyi kırpar, siyah padding ile kare yapar, input_size'a ölçekler.

        Dönüş:
          resized, pad_left, pad_top, max_dim
        """
        cropped = frame[y: y + h, x: x + w]

        max_dim = max(w, h)

        pad_top = (max_dim - h) // 2
        pad_bottom = max_dim - h - pad_top
        pad_left = (max_dim - w) // 2
        pad_right = max_dim - w - pad_left

        squared = cv2.copyMakeBorder(
            cropped,
            pad_top,
            pad_bottom,
            pad_left,
            pad_right,
            cv2.BORDER_CONSTANT,
            value=[0, 0, 0],
        )

        resized = cv2.resize(
            squared,
            (self._input_size, self._input_size),
            interpolation=cv2.INTER_AREA,
        )

        return resized, pad_left, pad_top, max_dim

    def _box_to_original(
        self,
        xyxy: list,
        orig_x: int,
        orig_y: int,
        pad_left: int,
        pad_top: int,
        max_dim: int,
        frame_w: int,
        frame_h: int,
    ):
        """
        input_size x input_size üzerindeki YOLO kutusunu
        orijinal frame koordinatlarına normalize olarak çevirir.
        """
        scale = max_dim / float(self._input_size)

        abs_x1 = orig_x - pad_left + xyxy[0] * scale
        abs_y1 = orig_y - pad_top + xyxy[1] * scale
        abs_x2 = orig_x - pad_left + xyxy[2] * scale
        abs_y2 = orig_y - pad_top + xyxy[3] * scale

        return [
            max(0.0, abs_x1 / frame_w),
            max(0.0, abs_y1 / frame_h),
            min(1.0, abs_x2 / frame_w),
            min(1.0, abs_y2 / frame_h),
        ]

    # ── Tahmin ────────────────────────────────────────────────────────────

    def predict(self, frame: np.ndarray) -> List[Detection]:
        if self._backend == "pt":
            raw_detections = self._predict_pt_raw(frame)

        elif self._backend == "onnx":
            raw_detections = self._predict_onnx_raw(frame)

        else:
            raw_detections = self._demo_predict(frame)

        return self._apply_temporal_stability(raw_detections)

    # ── .pt yolu ──────────────────────────────────────────────────────────

    def _predict_pt_raw(self, frame: np.ndarray) -> List[Detection]:
        try:
            frame_h, frame_w = frame.shape[:2]
            detections: List[Detection] = []

            fig_rects = self._find_figs(frame)

            if not fig_rects:
                logger.debug("Frame'de incir adayı bulunamadı, YOLO atlandı.")
                return []

            crops = []
            crop_infos = []

            for (fx, fy, fw, fh) in fig_rects:
                resized, pad_left, pad_top, max_dim = self._crop_and_square(
                    frame,
                    fx,
                    fy,
                    fw,
                    fh,
                )

                crops.append(resized)

                crop_infos.append(
                    {
                        "fx": fx,
                        "fy": fy,
                        "pad_left": pad_left,
                        "pad_top": pad_top,
                        "max_dim": max_dim,
                    }
                )

            results = self._pt_model.predict(
                crops,
                conf=self._conf,
                iou=self._iou,
                imgsz=self._input_size,
                verbose=False,
            )

            for crop_index, r in enumerate(results):
                info = crop_infos[crop_index]

                if r.boxes is None or len(r.boxes) == 0:
                    continue

                for box in r.boxes:
                    cls_id = int(box.cls[0])
                    conf = float(box.conf[0])
                    xyxy = box.xyxy[0].tolist()

                    norm_bbox = self._box_to_original(
                        xyxy,
                        info["fx"],
                        info["fy"],
                        info["pad_left"],
                        info["pad_top"],
                        info["max_dim"],
                        frame_w,
                        frame_h,
                    )

                    detections.append(
                        Detection(
                            class_name=(
                                CLASS_NAMES[cls_id]
                                if cls_id < len(CLASS_NAMES)
                                else "Unknown"
                            ),
                            confidence=conf,
                            bbox=norm_bbox,
                        )
                    )

            return detections

        except Exception as e:
            logger.error(f"PT inference hatası: {e}")
            return []

    # ── .onnx yolu ────────────────────────────────────────────────────────

    def _predict_onnx_raw(self, frame: np.ndarray) -> List[Detection]:
        try:
            frame_h, frame_w = frame.shape[:2]
            detections: List[Detection] = []

            fig_rects = self._find_figs(frame)

            if not fig_rects:
                logger.debug("Frame'de incir adayı bulunamadı, ONNX inference atlandı.")
                return []

            for (fx, fy, fw, fh) in fig_rects:
                resized, pad_left, pad_top, max_dim = self._crop_and_square(
                    frame,
                    fx,
                    fy,
                    fw,
                    fh,
                )

                blob = self._preprocess(resized)
                outputs = self._session.run(None, {self._input_name: blob})
                crop_detections = self._postprocess_to_xyxy(outputs)

                for item in crop_detections:
                    class_id = item["class_id"]
                    conf = item["confidence"]
                    xyxy = item["xyxy"]

                    norm_bbox = self._box_to_original(
                        xyxy,
                        fx,
                        fy,
                        pad_left,
                        pad_top,
                        max_dim,
                        frame_w,
                        frame_h,
                    )

                    detections.append(
                        Detection(
                            class_name=(
                                CLASS_NAMES[class_id]
                                if class_id < len(CLASS_NAMES)
                                else "Unknown"
                            ),
                            confidence=conf,
                            bbox=norm_bbox,
                        )
                    )

            return self._nms(detections)

        except Exception as e:
            logger.error(f"ONNX inference hatası: {e}")
            return []

    def _preprocess(self, frame: np.ndarray) -> np.ndarray:
        img = cv2.resize(frame, (self._input_size, self._input_size))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = img.astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))

        return np.expand_dims(img, axis=0)

    def _postprocess_to_xyxy(self, outputs: list) -> List[Dict]:
        """
        ONNX çıktısını input_size koordinat sisteminde xyxy kutularına çevirir.
        """
        pred = outputs[0]

        if pred.ndim == 3:
            pred = pred[0].T

        raw_items = []

        for row in pred:
            scores = row[4:]
            class_id = int(np.argmax(scores))
            conf = float(scores[class_id])

            if conf < self._conf:
                continue

            cx, cy, w, h = row[:4]

            x1 = max(0.0, cx - w / 2)
            y1 = max(0.0, cy - h / 2)
            x2 = min(float(self._input_size), cx + w / 2)
            y2 = min(float(self._input_size), cy + h / 2)

            raw_items.append(
                {
                    "class_id": class_id,
                    "confidence": conf,
                    "xyxy": [float(x1), float(y1), float(x2), float(y2)],
                }
            )

        if not raw_items:
            return []

        return self._nms_raw_items(raw_items)

    # ── Temporal stability: 3 frame üst üste aynı karar ───────────────────

    def _apply_temporal_stability(self, detections: List[Detection]) -> List[Detection]:
        """
        Aynı bölgede aynı class 3 frame üst üste görülmeden detection'ı dışarı vermez.

        Örnek:
          Frame 1: Healthy → bekle
          Frame 2: Healthy → bekle
          Frame 3: Healthy → artık kabul et, çiz/kaydet

        Class değişirse aynı track sayılmaz.
        """
        if not detections:
            self._increase_missing_frames()
            return []

        accepted: List[Detection] = []
        matched_track_indexes = set()

        for det in detections:
            track_index = self._find_matching_track(det)

            if track_index is None:
                self._tracks.append(
                    {
                        "class_name": det.class_name,
                        "bbox": det.bbox,
                        "confidence": det.confidence,
                        "hits": 1,
                        "missing": 0,
                        "last_detection": det,
                    }
                )
                continue

            track = self._tracks[track_index]
            matched_track_indexes.add(track_index)

            track["bbox"] = det.bbox
            track["confidence"] = det.confidence
            track["hits"] += 1
            track["missing"] = 0
            track["last_detection"] = det

            if track["hits"] >= self._stability_required:
                accepted.append(det)

        for index, track in enumerate(self._tracks):
            if index not in matched_track_indexes:
                track["missing"] += 1

        self._remove_lost_tracks()

        return accepted

    def _find_matching_track(self, det: Detection) -> Optional[int]:
        best_index = None
        best_iou = 0.0

        for index, track in enumerate(self._tracks):
            if track["class_name"] != det.class_name:
                continue

            iou = self._bbox_iou(track["bbox"], det.bbox)

            if iou > best_iou:
                best_iou = iou
                best_index = index

        if best_iou >= self._stable_iou_threshold:
            return best_index

        return None

    def _increase_missing_frames(self):
        for track in self._tracks:
            track["missing"] += 1

        self._remove_lost_tracks()

    def _remove_lost_tracks(self):
        self._tracks = [
            track
            for track in self._tracks
            if track["missing"] <= self._max_missing_frames
        ]

    def _bbox_iou(self, a: List[float], b: List[float]) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b

        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)

        inter_w = max(0.0, inter_x2 - inter_x1)
        inter_h = max(0.0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h

        area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
        area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)

        return inter_area / (area_a + area_b - inter_area + 1e-6)

    # ── NMS ───────────────────────────────────────────────────────────────

    def _nms(self, detections: List[Detection]) -> List[Detection]:
        if not detections:
            return []

        boxes = np.array(
            [[d.bbox[0], d.bbox[1], d.bbox[2], d.bbox[3]] for d in detections]
        )

        scores = np.array([d.confidence for d in detections])
        order = scores.argsort()[::-1]

        indices = []

        while order.size > 0:
            i = order[0]
            indices.append(i)

            if order.size == 1:
                break

            xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
            yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
            xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
            yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])

            inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)

            area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])

            area_o = (
                (boxes[order[1:], 2] - boxes[order[1:], 0])
                * (boxes[order[1:], 3] - boxes[order[1:], 1])
            )

            iou = inter / (area_i + area_o - inter + 1e-6)

            order = order[1:][iou < self._iou]

        return [detections[i] for i in indices]

    def _nms_raw_items(self, items: List[Dict]) -> List[Dict]:
        if not items:
            return []

        boxes = np.array([item["xyxy"] for item in items])
        scores = np.array([item["confidence"] for item in items])
        order = scores.argsort()[::-1]

        indices = []

        while order.size > 0:
            i = order[0]
            indices.append(i)

            if order.size == 1:
                break

            xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
            yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
            xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
            yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])

            inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)

            area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])

            area_o = (
                (boxes[order[1:], 2] - boxes[order[1:], 0])
                * (boxes[order[1:], 3] - boxes[order[1:], 1])
            )

            iou = inter / (area_i + area_o - inter + 1e-6)

            order = order[1:][iou < self._iou]

        return [items[i] for i in indices]

    # ── Demo modu ─────────────────────────────────────────────────────────

    def _demo_predict(self, frame: np.ndarray) -> List[Detection]:
        """
        Gerçek model olmadan test için demo detection üretir.
        Bu sonuçlar da 3-frame stability filtresinden geçer.
        """
        time.sleep(0.03)

        roll = self._rng.random()

        if roll < 0.33:
            return []

        if roll < 0.45:
            conf = float(self._rng.uniform(0.72, 0.97))

            return [
                Detection(
                    "Aflatoxin",
                    conf,
                    [0.20, 0.20, 0.70, 0.70],
                )
            ]

        conf = float(self._rng.uniform(0.68, 0.97))

        return [
            Detection(
                "Healthy",
                conf,
                [0.20, 0.20, 0.70, 0.70],
            )
        ]

    # ── Ayarlar ───────────────────────────────────────────────────────────

    def set_conf_threshold(self, value: float):
        self._conf = value

    @property
    def is_demo_mode(self) -> bool:
        return self._demo_mode

    @property
    def backend(self) -> str:
        return self._backend or "demo"
