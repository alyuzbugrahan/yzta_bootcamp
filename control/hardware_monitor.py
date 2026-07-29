import cv2
from utils.logger import logger
from utils.config_manager import ConfigManager


class HardwareStatus:
    def __init__(self, camera_ok: bool, message: str = ""):
        self.camera_ok = camera_ok
        self.message = message

    @property
    def all_ok(self) -> bool:
        return self.camera_ok


class HardwareMonitor:
    """
    Sistem başlangıcında ve isteğe bağlı olarak
    donanım bağlantısını kontrol eder.
    Röle devre dışı — UV ışık manuel açılıyor.
    """

    def __init__(self):
        cfg = ConfigManager()
        self._camera_index = cfg.get_int("camera", "camera_index", 0)

    def check(self) -> HardwareStatus:
        camera_ok = self._ping_camera()
        msg = "Tüm donanım hazır." if camera_ok else "Kamera bulunamadı!"
        status = HardwareStatus(camera_ok=camera_ok, message=msg)
        logger.info(f"Donanım kontrolü: kamera={'OK' if camera_ok else 'HATA'}")
        return status

    def _ping_camera(self) -> bool:
        cap = cv2.VideoCapture(self._camera_index)
        ok = cap.isOpened()
        cap.release()
        return ok
