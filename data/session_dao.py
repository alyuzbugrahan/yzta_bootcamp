import sqlite3
import csv
from datetime import datetime
from typing import Optional, List
from utils.logger import logger


class SessionDAO:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # ── Yazma ─────────────────────────────────────────────────────────────
    def create_session(self, batch_id: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO sessions (batch_id, start_time) VALUES (?, ?)",
            (batch_id, datetime.now().isoformat())
        )
        self._conn.commit()
        logger.info(f"Yeni oturum oluşturuldu: {batch_id} (id={cur.lastrowid})")
        return cur.lastrowid

    def update_session_totals(self, session_id: int, total_figs: int, total_contaminated: int):
        self._conn.execute(
            """UPDATE sessions
               SET end_time=?, total_count=?, defect_count=?
               WHERE id=?""",
            (datetime.now().isoformat(), total_figs, total_contaminated, session_id)
        )
        self._conn.commit()

    # ── Okuma ─────────────────────────────────────────────────────────────
    def get_session(self, session_id: int) -> Optional[sqlite3.Row]:
        cur = self._conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,))
        return cur.fetchone()

    def get_all_sessions(self) -> List[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM sessions ORDER BY id DESC"
        )
        return cur.fetchall()

    def get_summary(self, session_id: int) -> dict:
        """Bir oturum için özet istatistikleri döndürür."""
        s = self.get_session(session_id)
        if not s:
            return {}

        cur = self._conn.execute(
            """SELECT
                 COUNT(*)                          AS total,
                 SUM(decision = 'Aflatoxin')       AS aflatoxin,
                 SUM(decision = 'Healthy')          AS healthy,
                 AVG(confidence)                    AS avg_conf,
                 AVG(latency_ms)                    AS avg_lat,
                 MIN(latency_ms)                    AS min_lat,
                 MAX(latency_ms)                    AS max_lat
               FROM inspections
               WHERE session_id = ?""",
            (session_id,)
        )
        row = cur.fetchone()
        total = row["total"] or 0
        aflatoxin = row["aflatoxin"] or 0
        return {
            "session_id":   session_id,
            "batch_id":     s["batch_id"],
            "start_time":   s["start_time"],
            "end_time":     s["end_time"],
            "total":        total,
            "aflatoxin":    aflatoxin,
            "healthy":      row["healthy"] or 0,
            "ratio_pct":    round(aflatoxin / total * 100, 2) if total > 0 else 0.0,
            "avg_conf":     round(row["avg_conf"] or 0, 4),
            "avg_lat_ms":   round(row["avg_lat"] or 0, 1),
            "min_lat_ms":   round(row["min_lat"] or 0, 1),
            "max_lat_ms":   round(row["max_lat"] or 0, 1),
        }

    # ── CSV dışa aktarma (RFC 4180 uyumlu) ───────────────────────────────
    def export_to_csv(self, session_id: int, file_path: str) -> bool:
        try:
            cur = self._conn.execute(
                """SELECT
                     i.fig_seq,
                     s.batch_id,
                     i.timestamp,
                     i.decision,
                     i.confidence,
                     i.latency_ms,
                     i.image_path
                   FROM inspections i
                   JOIN sessions s ON s.id = i.session_id
                   WHERE i.session_id = ?
                   ORDER BY i.fig_seq""",
                (session_id,)
            )
            rows = cur.fetchall()
            with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Fig_ID", "Batch_ID", "Timestamp",
                    "Decision", "Confidence", "Latency_ms", "Image_Path"
                ])
                for r in rows:
                    writer.writerow([
                        r["fig_seq"],
                        r["batch_id"],
                        r["timestamp"],
                        r["decision"],
                        f"{r['confidence']:.4f}",
                        f"{r['latency_ms']:.1f}",
                        r["image_path"] or "",
                    ])
            logger.info(f"CSV dışa aktarıldı: {file_path} ({len(rows)} kayıt)")
            return True
        except Exception as e:
            logger.error(f"CSV dışa aktarma hatası: {e}")
            return False
