"""
Figion — DatabaseHandler
========================
Rapor şeması (HLD Section 2.4 + LLD Section 3.2) ile birebir eşleşen
SQLite veritabanı katmanı.

Tablo yapısı (ERD):
  sessions     ──┐
  inspections  ──┘  (session_id FK)

PRAGMA'lar:
  • journal_mode = WAL   → eş zamanlı okuma/yazma, çökmeye dayanıklı
  • foreign_keys  = ON   → referans bütünlüğü zorlaması
  • synchronous   = NORMAL → WAL ile güvenli, performanslı
  • cache_size    = -8000 → 8 MB bellek içi önbellek
"""

import sqlite3
import os
from utils.config_manager import ConfigManager
from utils.logger import logger


# ── Şema sabitleri ─────────────────────────────────────────────────────────
_DDL = """
-- ── sessions tablosu ──────────────────────────────────────────────────────
-- Her "Başlat → Durdur" döngüsü bir oturuma karşılık gelir.
-- batch_id  : BATCH_YYYYMMDD_HHmmss formatında üretilir, UNIQUE kısıtı
--             sayesinde aynı batch_id'ye sahip iki oturum oluşamaz.
-- total_count / defect_count : Oturum kapanırken güncellenir (UPDATE).
CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id      TEXT    NOT NULL UNIQUE,
    start_time    TEXT    NOT NULL,          -- ISO-8601
    end_time      TEXT,                      -- NULL iken oturum açık
    total_count   INTEGER NOT NULL DEFAULT 0,
    defect_count  INTEGER NOT NULL DEFAULT 0,
    CHECK (total_count  >= 0),
    CHECK (defect_count >= 0),
    CHECK (defect_count <= total_count)
);

-- ── inspections tablosu ───────────────────────────────────────────────────
-- Konveyör bandından geçen her incirin tek kayıtı.
-- fig_seq    : Oturum içi sıra numarası (1, 2, 3 …)
-- decision   : "Healthy" veya "Aflatoxin" — başka değer kabul edilmez
-- confidence : Modelin güven skoru [0.0 – 1.0]
-- latency_ms : Görüntü alındı → karar verildi süresi (milisaniye)
-- image_path : Diskteki JPEG dosyasının tam yolu (arşiv)
CREATE TABLE IF NOT EXISTS inspections (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    fig_seq       INTEGER NOT NULL,
    timestamp     TEXT    NOT NULL,          -- ISO-8601
    decision      TEXT    NOT NULL CHECK (decision IN ('Healthy', 'Aflatoxin')),
    confidence    REAL    NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
    latency_ms    REAL    NOT NULL DEFAULT 0.0,
    image_path    TEXT,
    UNIQUE (session_id, fig_seq)             -- bir oturumda aynı sıra no tekrarsız
);

-- ── İndeksler ─────────────────────────────────────────────────────────────
-- Görüntüleyicide sık yapılan sorgular için:
CREATE INDEX IF NOT EXISTS idx_insp_session
    ON inspections (session_id);

CREATE INDEX IF NOT EXISTS idx_insp_decision
    ON inspections (session_id, decision);

CREATE INDEX IF NOT EXISTS idx_sessions_batch
    ON sessions (batch_id);
"""


class DatabaseConnectionFactory:
    """
    Tek ve paylaşılabilir SQLite bağlantısı döndüren fabrika.
    WAL modu + yabancı anahtar zorlaması + row_factory açık olarak döner.
    """

    @staticmethod
    def get_connection(db_path: str) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # ACID + performans PRAGMA'ları
        conn.executescript("""
            PRAGMA journal_mode = WAL;
            PRAGMA foreign_keys = ON;
            PRAGMA synchronous   = NORMAL;
            PRAGMA cache_size    = -8000;
            PRAGMA temp_store    = MEMORY;
        """)
        return conn


class DatabaseHandler:
    """
    Uygulama genelinde tek veritabanı nesnesi (MainWindow tarafından
    oluşturulur, tüm DAO'lara bağlantı referansı geçirilir).
    """

    def __init__(self):
        cfg = ConfigManager()
        self._db_path = cfg.get_path("database", "db_path", "data/figion.db")
        self._conn = DatabaseConnectionFactory.get_connection(self._db_path)
        self._apply_schema()
        logger.info(f"SQLite veritabanı hazır: {self._db_path}")

    def _apply_schema(self):
        """DDL'yi uygular — tablolar zaten varsa dokunmaz (IF NOT EXISTS)."""
        self._conn.executescript(_DDL)
        self._conn.commit()
        logger.debug("Şema uygulandı / doğrulandı.")

    def get_connection(self) -> sqlite3.Connection:
        return self._conn

    def db_path(self) -> str:
        return self._db_path

    def integrity_check(self) -> bool:
        """SQLite'ın yerleşik bütünlük kontrolünü çalıştırır."""
        cur = self._conn.execute("PRAGMA integrity_check")
        result = cur.fetchone()[0]
        ok = result == "ok"
        if not ok:
            logger.error(f"Veritabanı bütünlük hatası: {result}")
        return ok

    def vacuum(self):
        """Silinmiş kayıtlardan kalan boşluğu geri kazanır."""
        self._conn.execute("VACUUM")
        logger.info("VACUUM tamamlandı.")

    def close(self):
        if self._conn:
            self._conn.close()
            logger.info("Veritabanı bağlantısı kapatıldı.")
