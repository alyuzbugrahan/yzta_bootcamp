"""
Figion — Veritabanı Görüntüleyici Penceresi
============================================
Oturumları listeler, her oturumun detay kayıtlarını gösterir,
grafik çizer, CSV/Excel dışa aktarır, kayıt siler.
"""

import os
import sqlite3
import csv
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QWidget, QLabel, QPushButton,
    QHBoxLayout, QVBoxLayout, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QMessageBox, QFileDialog,
    QFrame, QStackedWidget, QComboBox, QLineEdit,
    QGroupBox, QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSortFilterProxyModel, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QLinearGradient
from PyQt6.QtCharts import (
    QChart, QChartView, QBarSeries, QBarSet,
    QBarCategoryAxis, QValueAxis, QPieSeries,
)

from data.database_handler import DatabaseHandler
from utils.logger import logger


# ─────────────────────────────────────────────────────────────────────────────
#  Renk sabitleri
# ─────────────────────────────────────────────────────────────────────────────
C_BG      = "#1a1a1a"
C_PANEL   = "#1e1e1e"
C_BORDER  = "#2e2e2e"
C_GREEN   = "#1D9E75"
C_RED     = "#E24B4A"
C_AMBER   = "#EF9F27"
C_TEXT    = "#e0e0e0"
C_MUTED   = "#666666"


# ─────────────────────────────────────────────────────────────────────────────
#  Yardımcı: sayısal tablo hücresi (sıralanabilir)
# ─────────────────────────────────────────────────────────────────────────────
class NumericItem(QTableWidgetItem):
    def __init__(self, value):
        super().__init__(str(value))
        self._val = value

    def __lt__(self, other):
        try:
            return float(self._val) < float(other._val)
        except Exception:
            return super().__lt__(other)


# ─────────────────────────────────────────────────────────────────────────────
#  Oturum listesi paneli (sol)
# ─────────────────────────────────────────────────────────────────────────────
class SessionListPanel(QWidget):
    session_selected = pyqtSignal(int, str)   # session_id, batch_id

    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(parent)
        self._conn = conn
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── başlık ──
        hdr = QWidget()
        hdr.setStyleSheet(f"background:{C_PANEL}; border-bottom:1px solid {C_BORDER};")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(12, 10, 12, 10)
        lbl = QLabel("SESSIONS")
        lbl.setStyleSheet(f"color:{C_MUTED}; font-size:11px; letter-spacing:1px;")
        self._refresh_btn = QPushButton("↻")
        self._refresh_btn.setFixedSize(26, 26)
        self._refresh_btn.setStyleSheet(
            f"background:transparent; color:{C_MUTED}; border:none; font-size:14px;"
            f"border-radius:4px;"
        )
        self._refresh_btn.setToolTip("Yenile")
        self._refresh_btn.clicked.connect(self.load)
        hdr_lay.addWidget(lbl)
        hdr_lay.addStretch()
        hdr_lay.addWidget(self._refresh_btn)
        lay.addWidget(hdr)

        # ── tablo ──
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Batch ID", "Start", "Total", "Aflatoxin"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(2, 60)
        self._table.setColumnWidth(3, 70)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(
            f"QTableWidget {{ background:{C_BG}; alternate-background-color:#202020; border:none; }}"
            f"QTableWidget::item {{ padding:4px 8px; color:{C_TEXT}; }}"
            f"QTableWidget::item:selected {{ background:#2a3a30; }}"
            f"QHeaderView::section {{ background:{C_PANEL}; color:{C_MUTED}; font-size:11px;"
            f"  padding:5px 8px; border:none; border-bottom:1px solid {C_BORDER}; }}"
        )
        self._table.itemSelectionChanged.connect(self._on_select)
        lay.addWidget(self._table, stretch=1)

        # YENİ: Event loop'un dialogu render etmesini beklemek için singleShot kullanılıyor
        QTimer.singleShot(0, self.load)

    def load(self):
        self._table.setRowCount(0)
        cur = self._conn.execute(
            "SELECT id, batch_id, start_time, total_count, defect_count "
            "FROM sessions ORDER BY id DESC"
        )
        rows = cur.fetchall()
        for r in rows:
            row = self._table.rowCount()
            self._table.insertRow(row)

            batch_item = QTableWidgetItem(r["batch_id"])
            batch_item.setData(Qt.ItemDataRole.UserRole, r["id"])

            start_str = r["start_time"][:16].replace("T", " ") if r["start_time"] else "—"

            defect_ratio = 0
            if r["total_count"] and r["total_count"] > 0:
                defect_ratio = r["defect_count"] / r["total_count"] * 100

            dirty_item = NumericItem(r["defect_count"] or 0)
            dirty_item.setForeground(
                QColor(C_RED) if (r["defect_count"] or 0) > 0 else QColor(C_MUTED)
            )
            dirty_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            total_item = NumericItem(r["total_count"] or 0)
            total_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            start_item = QTableWidgetItem(start_str)
            start_item.setForeground(QColor(C_MUTED))

            self._table.setItem(row, 0, batch_item)
            self._table.setItem(row, 1, start_item)
            self._table.setItem(row, 2, total_item)
            self._table.setItem(row, 3, dirty_item)
            self._table.setRowHeight(row, 30)

        if self._table.rowCount() > 0:
            self._table.selectRow(0)

    def _on_select(self):
        rows = self._table.selectedItems()
        if not rows:
            return
        item = self._table.item(self._table.currentRow(), 0)
        if item:
            sid = item.data(Qt.ItemDataRole.UserRole)
            bid = item.text()
            self.session_selected.emit(sid, bid)

    def selected_session_id(self) -> int:
        item = self._table.item(self._table.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None


# ─────────────────────────────────────────────────────────────────────────────
#  Oturum detayları paneli (sağ üst)
# ─────────────────────────────────────────────────────────────────────────────
class InspectionTablePanel(QWidget):
    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(parent)
        self._conn = conn
        self._session_id = None
        self._all_rows = []
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── araç çubuğu ──
        toolbar = QWidget()
        toolbar.setStyleSheet(
            f"background:{C_PANEL}; border-bottom:1px solid {C_BORDER};"
        )
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(12, 8, 12, 8)
        tb_lay.setSpacing(8)

        lbl = QLabel("RECORDS")
        lbl.setStyleSheet(f"color:{C_MUTED}; font-size:11px; letter-spacing:1px;")

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.setFixedWidth(160)
        self._search.setStyleSheet(
            f"background:#252525; border:1px solid #3a3a3a; border-radius:4px;"
            f"padding:3px 8px; color:{C_TEXT}; font-size:12px;"
        )
        self._search.textChanged.connect(self._apply_filter)

        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["Total", "Healthy", "Aflatoxin"])
        self._filter_combo.setFixedWidth(110)
        self._filter_combo.setStyleSheet(
            f"background:#252525; border:1px solid #3a3a3a; border-radius:4px;"
            f"padding:3px 6px; color:{C_TEXT}; font-size:12px;"
        )
        self._filter_combo.currentIndexChanged.connect(self._apply_filter)

        self._count_lbl = QLabel("0 records")
        self._count_lbl.setStyleSheet(f"color:{C_MUTED}; font-size:11px;")

        tb_lay.addWidget(lbl)
        tb_lay.addStretch()
        tb_lay.addWidget(self._search)
        tb_lay.addWidget(self._filter_combo)
        tb_lay.addWidget(self._count_lbl)

        lay.addWidget(toolbar)

        # ── tablo ──
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Fig ID", "Timestamp", "Decision", "Confidence", "Latency (ms)", "Image"]
        )
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)

        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        self._table.setColumnWidth(0, 70)
        self._table.setColumnWidth(2, 100)

        #self._table.setColumnWidth(3, 65)
        #self._table.setColumnWidth(4, 90)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.setStyleSheet(
            f"QTableWidget {{ background:{C_BG}; alternate-background-color:#202020; border:none; }}"
            f"QTableWidget::item {{ padding:3px 8px; color:{C_TEXT}; font-size:12px; }}"
            f"QTableWidget::item:selected {{ background:#2a3a30; }}"
            f"QHeaderView::section {{ background:{C_PANEL}; color:{C_MUTED}; font-size:11px;"
            f"  padding:5px 8px; border:none; border-bottom:1px solid {C_BORDER}; }}"
        )
        lay.addWidget(self._table, stretch=1)

    def load_session(self, session_id: int):
        self._session_id = session_id
        cur = self._conn.execute(
            "SELECT fig_seq, timestamp, decision, confidence, latency_ms, image_path "
            "FROM inspections WHERE session_id=? ORDER BY fig_seq",
            (session_id,)
        )
        self._all_rows = cur.fetchall()
        self._apply_filter()

    def _apply_filter(self):
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)

        text   = self._search.text().lower()
        ftype  = self._filter_combo.currentText()

        count = 0
        for r in self._all_rows:
            decision = r["decision"]
            if ftype == "Healthy"   and decision != "Healthy":    continue
            if ftype == "Aflatoxin" and decision != "Aflatoxin":  continue
            ts_str = r["timestamp"][:16].replace("T", " ") if r["timestamp"] else ""
            if text and text not in ts_str.lower() and text not in decision.lower():
                continue

            row = self._table.rowCount()
            self._table.insertRow(row)

            id_item = NumericItem(r["fig_seq"])
            id_item.setForeground(QColor(C_MUTED))
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            ts_item = QTableWidgetItem(ts_str)
            ts_item.setForeground(QColor(C_MUTED))

            is_bad = decision == "Aflatoxin"
            dec_item = QTableWidgetItem("Aflatoxin" if is_bad else "Healthy")
            dec_item.setForeground(QColor(C_RED if is_bad else C_GREEN))
            dec_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            conf_val = round(float(r["confidence"]) * 100, 1)
            conf_item = NumericItem(f"{conf_val:.1f}%")
            conf_item.setForeground(QColor(C_TEXT))
            conf_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            lat = r["latency_ms"] or 0
            lat_item = NumericItem(f"{lat:.0f}")
            lat_item.setForeground(QColor(C_MUTED))
            lat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            img_path = r["image_path"] or ""
            img_item = QTableWidgetItem(os.path.basename(img_path) if img_path else "—")
            img_item.setForeground(QColor(C_MUTED))
            img_item.setToolTip(img_path)

            self._table.setItem(row, 0, id_item)
            self._table.setItem(row, 1, ts_item)
            self._table.setItem(row, 2, dec_item)
            self._table.setItem(row, 3, conf_item)
            self._table.setItem(row, 4, lat_item)
            self._table.setItem(row, 5, img_item)
            self._table.setRowHeight(row, 26)
            count += 1

        self._table.setSortingEnabled(True)
        self._count_lbl.setText(f"{count} records")

    def get_visible_rows(self):
        """Görünen satırları (filtre sonrası) döndür."""
        return [
            {
                "fig_seq":    self._table.item(r, 0).text(),
                "timestamp":  self._table.item(r, 1).text(),
                "decision":   self._table.item(r, 2).text(),
                "confidence": self._table.item(r, 3).text(),
                "latency_ms": self._table.item(r, 4).text(),
                "image_path": self._table.item(r, 5).toolTip(),
            }
            for r in range(self._table.rowCount())
        ]


# ─────────────────────────────────────────────────────────────────────────────
#  Grafik paneli (sağ alt)
# ─────────────────────────────────────────────────────────────────────────────
class ChartPanel(QWidget):
    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(parent)
        self._conn = conn
        self._build()

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)

        # ── Pasta grafik ──
        self._pie_view = QChartView()
        self._pie_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._pie_view.setMinimumHeight(200)
        self._pie_view.setStyleSheet(f"background:{C_BG}; border:1px solid {C_BORDER}; border-radius:6px;")

        # ── Bar grafik ──
        self._bar_view = QChartView()
        self._bar_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._bar_view.setStyleSheet(f"background:{C_BG}; border:1px solid {C_BORDER}; border-radius:6px;")

        lay.addWidget(self._pie_view, stretch=1)
        lay.addWidget(self._bar_view, stretch=2)

    def load_session(self, session_id: int):
        cur = self._conn.execute(
            "SELECT decision, confidence FROM inspections WHERE session_id=?",
            (session_id,)
        )
        rows = cur.fetchall()

        healthy   = sum(1 for r in rows if r["decision"] == "Healthy")
        aflatoxin = sum(1 for r in rows if r["decision"] == "Aflatoxin")
        total     = healthy + aflatoxin

        self._draw_pie(healthy, aflatoxin, total)
        self._draw_bar(rows)

    def _draw_pie(self, healthy: int, aflatoxin: int, total: int):
        series = QPieSeries()
        if total == 0:
            s = series.append("Veri Yok", 1)
            s.setBrush(QBrush(QColor("#333")))
        else:
            s_h = series.append(f"Sağlıklı  {healthy}", healthy)
            s_h.setBrush(QBrush(QColor(C_GREEN)))
            s_h.setLabelVisible(True)
            s_a = series.append(f"Aflatoksin  {aflatoxin}", aflatoxin)
            s_a.setBrush(QBrush(QColor(C_RED)))
            s_a.setLabelVisible(True)

        chart = QChart()
        chart.addSeries(series)
        chart.setTitle(f"Distribution  (n={total})")
        chart.setTitleBrush(QBrush(QColor(C_TEXT)))
        chart.setBackgroundBrush(QBrush(QColor(C_BG)))
        chart.legend().setLabelColor(QColor(C_MUTED))
        chart.setMargins(__import__("PyQt6.QtCore", fromlist=["QMargins"]).QMargins(4, 4, 4, 4))
        self._pie_view.setChart(chart)

    def _draw_bar(self, rows: list):
        # Confidence score distribution — 10 bins
        buckets = [0] * 10
        for r in rows:
            idx = min(int(float(r["confidence"]) * 10), 9)
            buckets[idx] += 1

        bar_set = QBarSet("Record Count")
        bar_set.setColor(QColor(C_GREEN))
        for v in buckets:
            bar_set.append(v)

        series = QBarSeries()
        series.append(bar_set)

        cats = [f"{i*10}–{i*10+9}%" for i in range(10)]
        axis_x = QBarCategoryAxis()
        axis_x.append(cats)
        axis_x.setLabelsColor(QColor(C_MUTED))

        axis_y = QValueAxis()
        axis_y.setLabelFormat("%d")
        axis_y.setLabelsColor(QColor(C_MUTED))

        chart = QChart()
        chart.addSeries(series)
        chart.setTitle("Confidence Score Distribution")
        chart.setTitleBrush(QBrush(QColor(C_TEXT)))
        chart.setBackgroundBrush(QBrush(QColor(C_BG)))
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(axis_x)
        series.attachAxis(axis_y)
        chart.legend().hide()
        chart.setMargins(__import__("PyQt6.QtCore", fromlist=["QMargins"]).QMargins(4, 4, 4, 4))
        self._bar_view.setChart(chart)


# ─────────────────────────────────────────────────────────────────────────────
#  Summary statistics cards row
# ─────────────────────────────────────────────────────────────────────────────
class SummaryBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(72)
        self.setStyleSheet(f"background:{C_PANEL}; border-bottom:1px solid {C_BORDER};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(24)

        self._cards = {}
        for key, label, color in [
            ("total",    "Total",        C_TEXT),
            ("healthy",  "Healthy",      C_GREEN),
            ("afla",     "Aflatoxin",    C_RED),
            ("ratio",    "Contamination Rate", C_AMBER),
            ("duration", "Duration",          C_MUTED),
            ("avg_lat",  "Avg. Latency",  C_MUTED),
        ]:
            card = QWidget()
            c_lay = QVBoxLayout(card)
            c_lay.setContentsMargins(0, 0, 0, 0)
            c_lay.setSpacing(2)
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color:{C_MUTED}; font-size:10px; letter-spacing:1px;")
            val = QLabel("—")
            val.setStyleSheet(f"color:{color}; font-size:18px; font-weight:600;")
            c_lay.addWidget(lbl)
            c_lay.addWidget(val)
            self._cards[key] = val
            lay.addWidget(card)

        lay.addStretch()

    def update(self, conn: sqlite3.Connection, session_id: int):
        cur = conn.execute(
            "SELECT total_count, defect_count, start_time, end_time FROM sessions WHERE id=?",
            (session_id,)
        )
        s = cur.fetchone()
        if not s:
            return

        total  = s["total_count"] or 0
        defect = s["defect_count"] or 0
        healthy = total - defect
        ratio  = f"{defect/total*100:.1f}%" if total > 0 else "—"

        duration = "—"
        if s["start_time"] and s["end_time"]:
            try:
                t0 = datetime.fromisoformat(s["start_time"])
                t1 = datetime.fromisoformat(s["end_time"])
                secs = int((t1 - t0).total_seconds())
                duration = f"{secs//60}m {secs%60}s"
            except Exception:
                pass

        cur2 = conn.execute(
            "SELECT AVG(latency_ms) as avg_lat FROM inspections WHERE session_id=?",
            (session_id,)
        )
        avg_row = cur2.fetchone()
        avg_lat = f"{avg_row['avg_lat']:.0f} ms" if avg_row and avg_row["avg_lat"] else "—"

        self._cards["total"].setText(str(total))
        self._cards["healthy"].setText(str(healthy))
        self._cards["afla"].setText(str(defect))
        self._cards["ratio"].setText(ratio)
        self._cards["duration"].setText(duration)
        self._cards["avg_lat"].setText(avg_lat)


# ─────────────────────────────────────────────────────────────────────────────
#  Ana DB Viewer Penceresi
# ─────────────────────────────────────────────────────────────────────────────
class DatabaseViewer(QDialog):
    def __init__(self, db: DatabaseHandler, parent=None):
        super().__init__(parent)
        self._conn = db.get_connection()
        self._current_session_id = None
        self._current_batch_id   = None
        self._build()
        self.setStyleSheet(self._stylesheet())

    # ── İnşa ──────────────────────────────────────────────────────────────
    def _build(self):
        self.setWindowTitle("Figion — Database Viewer")
        # self.resize(1300, 780)
        self.setMinimumSize(1000, 600)
  
        self.showMaximized()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── başlık çubuğu ──
        root.addWidget(self._build_header())

        # ── özet bar ──
        self._summary = SummaryBar()
        root.addWidget(self._summary)

        # ── gövde: sol liste | sağ detay ──
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(f"QSplitter::handle {{ background:{C_BORDER}; }}")

        # sol
        self._session_panel = SessionListPanel(self._conn)
        self._session_panel.setMinimumWidth(280)
        self._session_panel.setMaximumWidth(380)
        
        # sağ: dikey bölünme — tablo üst, grafikler alt
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.setHandleWidth(1)
        right_splitter.setStyleSheet(f"QSplitter::handle {{ background:{C_BORDER}; }}")

        self._inspection_panel = InspectionTablePanel(self._conn)
        right_splitter.addWidget(self._inspection_panel)

        self._chart_panel = ChartPanel(self._conn)
        self._chart_panel.setMinimumHeight(230)
        right_splitter.addWidget(self._chart_panel)
        right_splitter.setSizes([420, 240])

        splitter.addWidget(self._session_panel)
        splitter.addWidget(right_splitter)
        splitter.setSizes([300, 1000])

        root.addWidget(splitter, stretch=1)

        # ── alt çubuk ──
        root.addWidget(self._build_footer())
        
        # YENİ: Tüm widget'lar oluştuktan SONRA sinyali bağla
        self._session_panel.session_selected.connect(self._on_session_selected)

    def _build_header(self) -> QWidget:
        hdr = QWidget()
        hdr.setFixedHeight(48)
        hdr.setStyleSheet(f"background:{C_PANEL}; border-bottom:1px solid {C_BORDER};")
        lay = QHBoxLayout(hdr)
        lay.setContentsMargins(16, 0, 16, 0)

        title = QLabel("🗄  Database Viewer")
        title.setStyleSheet(f"color:{C_TEXT}; font-size:14px; font-weight:600;")

        self._session_lbl = QLabel("Select a session")
        self._session_lbl.setStyleSheet(f"color:{C_MUTED}; font-size:12px;")

        lay.addWidget(title)
        lay.addStretch()
        lay.addWidget(self._session_lbl)

        return hdr

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setFixedHeight(50)
        footer.setStyleSheet(f"background:{C_PANEL}; border-top:1px solid {C_BORDER};")
        lay = QHBoxLayout(footer)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(8)

        def btn(text, obj_name, tip=""):
            b = QPushButton(text)
            b.setObjectName(obj_name)
            b.setFixedHeight(32)
            if tip:
                b.setToolTip(tip)
            return b

        self._btn_csv     = btn("⬇  Export CSV",   "BtnExport", "Save the selected session as CSV")
        self._btn_del_ses = btn("🗑  Delete Session",       "BtnDanger", "Delete the selected session and its records")
        self._btn_del_all = btn("⚠  Clear All",    "BtnDanger", "Delete the ENTIRE database")
        self._btn_close   = btn("Close",                "BtnNeutral")

        self._btn_csv.clicked.connect(self._export_csv)
        self._btn_del_ses.clicked.connect(self._delete_session)
        self._btn_del_all.clicked.connect(self._delete_all)
        self._btn_close.clicked.connect(self.close)

        lay.addWidget(self._btn_csv)
        lay.addWidget(self._btn_del_ses)
        lay.addSpacing(8)
        lay.addWidget(self._btn_del_all)
        lay.addStretch()
        lay.addWidget(self._btn_close)

        return footer

    # ── Slot: oturum seçildi ───────────────────────────────────────────────
    def _on_session_selected(self, session_id: int, batch_id: str):
        self._current_session_id = session_id
        self._current_batch_id   = batch_id
        self._session_lbl.setText(f"Oturum: {batch_id}")
        self._summary.update(self._conn, session_id)
        self._inspection_panel.load_session(session_id)
        self._chart_panel.load_session(session_id)

    # ── CSV dışa aktar ────────────────────────────────────────────────────
    def _export_csv(self):
        if not self._current_session_id:
            QMessageBox.warning(self, "Warning", "Please select a session first.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Save CSV",
            f"{self._current_batch_id}.csv",
            "CSV Files (*.csv)"
        )
        if not path:
            return

        rows = self._inspection_panel.get_visible_rows()
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["Fig_ID", "Time", "Decision", "Confidence", "Latency_ms", "Image_Path"])
                for r in rows:
                    writer.writerow([
                        r["fig_seq"], r["timestamp"], r["decision"],
                        r["confidence"], r["latency_ms"], r["image_path"]
                    ])
            QMessageBox.information(self, "Success", f"CSV saved:\n{path}")
            logger.info(f"DB Viewer CSV export: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"CSV could not be saved:\n{e}")

    # ── Delete session ─────────────────────────────────────────────────────
    def _delete_session(self):
        if not self._current_session_id:
            QMessageBox.warning(self, "Warning", "Please select a session first.")
            return

        reply = QMessageBox.question(
            self, "Delete Session",
            f"'{self._current_batch_id}' session and all its records will be deleted.\nAre you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self._conn.execute("DELETE FROM inspections WHERE session_id=?", (self._current_session_id,))
            self._conn.execute("DELETE FROM sessions WHERE id=?", (self._current_session_id,))
            self._conn.commit()
            logger.info(f"Session deleted: {self._current_batch_id}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not delete:\n{e}")
            return

        self._current_session_id = None
        self._current_batch_id   = None
        self._session_lbl.setText("Select a session")
        self._inspection_panel.load_session(-1)
        self._session_panel.load()

    # ── Delete all ────────────────────────────────────────────────────────
    def _delete_all(self):
        reply = QMessageBox.question(
            self, "Delete All",
            "ALL sessions and records will be permanently deleted!\nThis action cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Second confirmation for such a destructive action 
        reply2 = QMessageBox.warning(
            self, "Final Confirmation",
            "Are you sure you want to delete the ENTIRE database?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply2 != QMessageBox.StandardButton.Yes:
            return

        try:
            self._conn.execute("DELETE FROM inspections")
            self._conn.execute("DELETE FROM sessions")
            self._conn.commit()
            logger.warning("Entire database cleared.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not clear database:\n{e}")
            return

        self._current_session_id = None
        self._current_batch_id   = None
        self._session_lbl.setText("Select a session")
        self._inspection_panel.load_session(-1)
        self._session_panel.load()

    # ── Style ──────────────────────────────────────────────────────────────
    def _stylesheet(self) -> str:
        return f"""
QDialog {{ background:{C_BG}; color:{C_TEXT}; font-family:"Segoe UI","Helvetica Neue",Arial,sans-serif; font-size:13px; }}
QPushButton {{ background:#2a2a2a; border:1px solid #3a3a3a; border-radius:5px;
               padding:5px 12px; color:#ccc; font-size:12px; }}
QPushButton:hover {{ background:#333; border-color:#555; }}
QPushButton#BtnExport {{ background:#0d2e20; color:{C_GREEN}; border-color:#1a5c3a; }}
QPushButton#BtnExport:hover {{ background:#143d29; }}
QPushButton#BtnDanger {{ background:#2e0d0d; color:{C_RED}; border-color:#5a1a1a; }}
QPushButton#BtnDanger:hover {{ background:#3d1010; }}
QPushButton#BtnNeutral {{ background:#252525; color:#888; border-color:#333; }}
QScrollBar:vertical {{ background:{C_BG}; width:6px; }}
QScrollBar::handle:vertical {{ background:#3a3a3a; border-radius:3px; min-height:20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
QToolTip {{ background:#2a2a2a; color:#ddd; border:1px solid #444; border-radius:4px; padding:4px 8px; }}
QMessageBox {{ background:{C_PANEL}; }}
"""