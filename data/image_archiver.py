import queue
import threading
import cv2
import numpy as np
from utils.logger import logger


class ImageArchiver:
    """
    Görüntüleri arka plan thread'inde diske kayıt eder.
    Ana thread'i bloklamaz (Producer-Consumer pattern).
    """

    def __init__(self):
        self._queue: queue.Queue = queue.Queue(maxsize=200)
        self._running = False
        self._thread: threading.Thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._writer_loop, daemon=True, name="ImageArchiver")
        self._thread.start()
        logger.info("ImageArchiver başlatıldı.")

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._queue.put(None)  # poison pill
            self._thread.join(timeout=3)
        logger.info("ImageArchiver durduruldu.")

    def enqueue_image(self, frame: np.ndarray, file_path: str):
        try:
            self._queue.put_nowait((frame.copy(), file_path))
        except queue.Full:
            logger.warning("ImageArchiver kuyruğu dolu, kare atlandı.")

    def _writer_loop(self):
        while self._running:
            try:
                item = self._queue.get(timeout=1.0)
                if item is None:
                    break
                frame, path = item
                cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
                self._queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Görüntü kaydedilemedi: {e}")
