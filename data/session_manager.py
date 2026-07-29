from datetime import datetime
from data.session_dao import SessionDAO
from data.inspection_repository import InspectionRepository
from data.image_archiver import ImageArchiver
from utils.dto import InspectionResult, SessionStats
from utils.path_builder import PathBuilder
from utils.logger import logger
import numpy as np


class SessionManager:
    def __init__(self, session_dao: SessionDAO, inspection_repo: InspectionRepository, archiver: ImageArchiver):
        self._dao = session_dao
        self._repo = inspection_repo
        self._archiver = archiver
        self._path_builder = PathBuilder()

        self._session_id: int = None
        self._batch_id: str = None
        self._fig_counter: int = 0
        self._stats = SessionStats()

    def start_new_session(self) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._batch_id = f"BATCH_{ts}"
        self._session_id = self._dao.create_session(self._batch_id)
        self._fig_counter = 0
        self._stats = SessionStats()
        logger.info(f"Oturum başlatıldı: {self._batch_id}")
        return self._batch_id

    def record_inspection(self, result: InspectionResult, frame: np.ndarray = None) -> InspectionResult:
        self._fig_counter += 1
        result.fig_id = self._fig_counter
        result.session_id = self._session_id
        result.batch_id = self._batch_id

        if frame is not None:
            img_path = self._path_builder.get_image_path(self._batch_id, self._fig_counter, result.decision)
            self._archiver.enqueue_image(frame, img_path)
            result.image_path = img_path

        self._repo.save(result)

        self._stats.total += 1
        if result.decision == "Aflatoxin":
            self._stats.aflatoxin += 1
        else:
            self._stats.healthy += 1

        return result

    def end_session(self):
        if self._session_id:
            self._dao.update_session_totals(self._session_id, self._stats.total, self._stats.aflatoxin)
            logger.info(f"Oturum sona erdi: {self._batch_id} — Toplam: {self._stats.total}, Kirli: {self._stats.aflatoxin}")

    def export_csv(self) -> str:
        if not self._session_id:
            return None
        path = self._path_builder.get_export_path(self._batch_id)
        self._dao.export_to_csv(self._session_id, path)
        return path

    @property
    def stats(self) -> SessionStats:
        return self._stats

    @property
    def session_id(self) -> int:
        return self._session_id

    @property
    def batch_id(self) -> str:
        return self._batch_id

    @property
    def fig_counter(self) -> int:
        return self._fig_counter
