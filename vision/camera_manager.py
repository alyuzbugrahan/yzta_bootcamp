import cv2
import numpy as np
from utils.config_manager import ConfigManager
from utils.logger import logger


class CameraNotFoundException(Exception):
    pass


class CameraManager:
    def __init__(self, camera_index: int = None, api_preference: int = cv2.CAP_ANY):
        cfg = ConfigManager()
        self._index = camera_index if camera_index is not None else cfg.get_int("camera", "camera_index", 0)
        self._api = api_preference
        self._cap: cv2.VideoCapture = None
        self._width = cfg.get_int("camera", "width", 1280)
        self._height = cfg.get_int("camera", "height", 720)

    def open_stream(self) -> bool:
        self._cap = cv2.VideoCapture(self._index, self._api)
        if not self._cap.isOpened():
            logger.error(f"Kamera açılamadı: index={self._index}")
            raise CameraNotFoundException(f"Kamera bulunamadı (index={self._index})")
        self._set_properties(self._width, self._height)
        logger.info(f"Kamera açıldı: index={self._index}, {self._width}x{self._height}")
        return True

    def read_frame(self) -> np.ndarray:
        if self._cap is None or not self._cap.isOpened():
            return None
        ret, frame = self._cap.read()
        if not ret:
            logger.warning("Kameradan kare okunamadı.")
            return None
        return frame

    def release(self):
        if self._cap and self._cap.isOpened():
            self._cap.release()
            logger.info("Kamera bağlantısı kapatıldı.")

    def is_opened(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def _set_properties(self, width: int, height: int):
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
