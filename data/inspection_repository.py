import sqlite3
from datetime import datetime
from utils.dto import InspectionResult
from utils.logger import logger


class InspectionRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def save(self, result: InspectionResult) -> int:
        try:
            cur = self._conn.execute(
                """INSERT INTO inspections
                   (session_id, fig_seq, timestamp, decision, confidence, latency_ms, image_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    result.session_id,
                    result.fig_id,
                    result.timestamp.isoformat(),
                    result.decision,
                    result.confidence,
                    result.latency_ms,
                    result.image_path,
                )
            )
            self._conn.commit()
            return cur.lastrowid
        except Exception as e:
            logger.error(f"Kayıt kaydedilemedi: {e}")
            return -1

    def get_recent(self, session_id: int, limit: int = 50):
        cur = self._conn.execute(
            """SELECT * FROM inspections
               WHERE session_id=?
               ORDER BY id DESC LIMIT ?""",
            (session_id, limit)
        )
        return cur.fetchall()
