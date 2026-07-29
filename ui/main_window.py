import os
import cv2
import numpy as np
from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QLabel, QPushButton,
    QHBoxLayout, QVBoxLayout, QGridLayout,
    QTableWidget, QTableWidgetItem, QSlider,
    QProgressBar, QSizePolicy, QMessageBox, QLineEdit,
    QFileDialog, QHeaderView, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSlot, QTimer
from PyQt6.QtGui import QImage, QPixmap, QColor

from ui.video_processor_worker import VideoProcessorWorker
from ui.widgets import StatCard, Separator
from ui.styles import DARK_STYLE
from ui.db_viewer import DatabaseViewer

from vision.camera_manager import CameraManager
from vision.inference_engine import YOLOONNXEngine
from control.hardware_monitor import HardwareMonitor
from control.state_manager import StateManager, AppState
from data.database_handler import DatabaseHandler
from data.session_dao import SessionDAO
from data.inspection_repository import InspectionRepository
from data.image_archiver import ImageArchiver
from data.session_manager import SessionManager
from utils.dto import InspectionResult, SessionStats
from utils.config_manager import ConfigManager
from utils.logger import logger


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._cfg             = ConfigManager()
        self._state_mgr       = StateManager()
        self._scan_start_time = None
        self._dur_timer       = QTimer(self)
        self._dur_timer.timeout.connect(self._update_duration)
        self._setup_data_layer()
        self._setup_vision_layer()
        self._build_ui()
        self.setStyleSheet(DARK_STYLE)
        self._perform_hw_check()
        self._state_mgr.transition(AppState.READY)

    # ------------------------------------------------------------------ #
    #  Initialization                                                      #
    # ------------------------------------------------------------------ #
    def _setup_data_layer(self):
        self._db              = DatabaseHandler()
        conn                  = self._db.get_connection()
        self._session_dao     = SessionDAO(conn)
        self._inspection_repo = InspectionRepository(conn)
        self._archiver        = ImageArchiver()
        self._archiver.start()
        self._session_mgr     = SessionManager(
            self._session_dao, self._inspection_repo, self._archiver
        )

    def _setup_vision_layer(self):
        self._camera = CameraManager()
        self._engine = YOLOONNXEngine()
        self._worker = VideoProcessorWorker(self._camera, self._engine)
        self._worker.frame_processed.connect(self._on_frame)
        self._worker.error_occurred.connect(self._on_camera_error)
        self._worker.inspection_ready.connect(self._on_inspection)

    # ------------------------------------------------------------------ #
    #  UI Construction                                                     #
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        self.setWindowTitle(self._cfg.get("app", "title", "Figion"))
        self.setMinimumSize(1100, 660)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_titlebar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_video_panel(), stretch=1)
        body.addWidget(self._build_right_panel(), stretch=0)
        root.addLayout(body, stretch=1)

    def _build_titlebar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TitleBar")
        bar.setFixedHeight(46)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 16, 0)


        

        title = QLabel("Aflatoxin Detection System")
        title.setObjectName("AppTitle")

        self._badge_cam = QLabel("● Camera")
        self._badge_cam.setObjectName("BadgeWarn")

        self._badge_demo = QLabel("⚠ Demo Mode")
        self._badge_demo.setObjectName("BadgeWarn")
        self._badge_demo.setVisible(False)

        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(self._badge_cam)
        layout.addWidget(self._badge_demo)
        layout.addSpacing(10)

        btn_db = QPushButton("🗄  Database")
        btn_db.setFixedHeight(28)
        btn_db.setStyleSheet(
            "background:#252525; border:1px solid #3a3a3a; border-radius:5px;"
            "padding:2px 12px; color:#aaa; font-size:12px;"
        )
        btn_db.clicked.connect(self._open_db_viewer)
        layout.addWidget(btn_db)

        return bar

    def _build_video_panel(self) -> QWidget:
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 6, 12)
        layout.setSpacing(8)

        frame_wrapper = QFrame()
        frame_wrapper.setObjectName("VideoFrame")
        frame_wrapper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        fw_layout = QVBoxLayout(frame_wrapper)
        fw_layout.setContentsMargins(0, 0, 0, 0)
        fw_layout.setSpacing(0)

        vid_header = QWidget()
        vh_lay = QHBoxLayout(vid_header)
        vh_lay.setContentsMargins(10, 6, 10, 6)

        vid_label = QLabel("Live Camera Feed")
        vid_label.setStyleSheet("color:#555; font-size:12px;")

        self._fps_label = QLabel("— ms")
        self._fps_label.setStyleSheet("color:#444; font-size:11px; font-family:monospace;")

        self._live_badge = QLabel("● LIVE")
        self._live_badge.setObjectName("BadgeErr")
        self._live_badge.setVisible(False)

        vh_lay.addWidget(vid_label)
        vh_lay.addStretch()
        vh_lay.addWidget(self._fps_label)
        vh_lay.addSpacing(8)
        vh_lay.addWidget(self._live_badge)
        fw_layout.addWidget(vid_header)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#1e1e1e;")
        fw_layout.addWidget(sep)

        self._video_label = QLabel("Starting camera…")
        self._video_label.setObjectName("VideoLabel")
        self._video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        fw_layout.addWidget(self._video_label, stretch=1)
        layout.addWidget(frame_wrapper, stretch=1)

        info = QLabel()
        info.setObjectName("SectionTitle")
        backend  = self._engine.backend.upper()
        demo_txt = (
            " | ⚠ NO MODEL — Demo mode active"
            if self._engine.is_demo_mode
            else f" | Backend: {backend}"
        )
        info.setText(f"YOLOv11n · {demo_txt}")
        layout.addWidget(info)

        return container

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("RightPanel")
        panel.setMinimumWidth(270)
        panel.setMaximumWidth(340)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_stats_section())
        layout.addWidget(Separator())
        layout.addWidget(self._build_control_section())
        layout.addWidget(Separator())
        layout.addWidget(self._build_session_section())
        layout.addWidget(Separator())
        layout.addWidget(self._build_log_section(), stretch=1)

        return panel

    def _build_stats_section(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        title = QLabel("SESSION STATISTICS")
        title.setObjectName("SectionTitle")
        lay.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(8)
        self._card_total   = StatCard("TOTAL",     "#e0e0e0")
        self._card_healthy = StatCard("HEALTHY",   "#1D9E75")
        self._card_bad     = StatCard("AFLATOXIN", "#E24B4A")
        self._card_ratio   = StatCard("DEFECT %",  "#EF9F27")
        self._card_kg      = StatCard("TOTAL KG",  "#7a8fa6")
        grid.addWidget(self._card_total,   0, 0)
        grid.addWidget(self._card_healthy, 0, 1)
        grid.addWidget(self._card_bad,     1, 0)
        grid.addWidget(self._card_ratio,   1, 1)
        grid.addWidget(self._card_kg,      2, 0, 1, 2)
        lay.addLayout(grid)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(5)
        lay.addWidget(self._progress)

        return w

    def _build_control_section(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        title = QLabel("CONTROL")
        title.setObjectName("SectionTitle")
        lay.addWidget(title)

        self._btn_start = QPushButton("▶  Start Scanning")
        self._btn_start.setObjectName("BtnStart")
        self._btn_start.setFixedHeight(38)
        self._btn_start.clicked.connect(self._on_start_clicked)

        self._btn_stop = QPushButton("■  Stop Scanning")
        self._btn_stop.setObjectName("BtnStop")
        self._btn_stop.setFixedHeight(38)
        self._btn_stop.setVisible(False)
        self._btn_stop.clicked.connect(self._on_stop_clicked)

        self._btn_new_batch = QPushButton("＋  New Batch")
        self._btn_new_batch.setObjectName("BtnNewBatch")
        self._btn_new_batch.setFixedHeight(38)
        self._btn_new_batch.setVisible(False)
        self._btn_new_batch.clicked.connect(self._on_new_batch_clicked)

        self._btn_export = QPushButton("⬇  Export CSV")
        self._btn_export.setObjectName("BtnExport")
        self._btn_export.setEnabled(False)
        self._btn_export.clicked.connect(self._on_export_clicked)

        lay.addWidget(self._btn_start)
        lay.addWidget(self._btn_stop)
        lay.addWidget(self._btn_new_batch)
        lay.addWidget(self._btn_export)

        # ── Fig weight input ──
        lay.addSpacing(4)
        kg_row = QHBoxLayout()
        kg_lbl = QLabel("Fig weight")
        kg_lbl.setStyleSheet("color:#777; font-size:12px;")
        self._fig_weight_input = QLineEdit()
        self._fig_weight_input.setText("10")
        self._fig_weight_input.setFixedWidth(48)
        self._fig_weight_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fig_weight_input.setStyleSheet(
            "background:#252525; border:1px solid #3a3a3a; border-radius:4px;"
            "padding:2px 6px; color:#aaa; font-size:12px;"
        )
        self._fig_weight_input.setPlaceholderText("g")
        self._fig_weight_input.textChanged.connect(self._update_stats)
        gr_lbl = QLabel("g / fig")
        gr_lbl.setStyleSheet("color:#555; font-size:11px;")
        kg_row.addWidget(kg_lbl)
        kg_row.addStretch()
        kg_row.addWidget(self._fig_weight_input)
        kg_row.addWidget(gr_lbl)
        lay.addLayout(kg_row)

        # ── Confidence threshold slider ──
        lay.addSpacing(4)
        conf_row = QHBoxLayout()
        conf_lbl = QLabel("Confidence threshold")
        conf_lbl.setStyleSheet("color:#777; font-size:12px;")
        self._conf_val_lbl = QLabel(f"{int(self._engine._conf * 100)}%")
        self._conf_val_lbl.setStyleSheet("color:#aaa; font-size:11px; font-family:monospace;")
        self._conf_val_lbl.setFixedWidth(34)
        self._conf_slider = QSlider(Qt.Orientation.Horizontal)
        self._conf_slider.setRange(30, 99)
        self._conf_slider.setValue(int(self._engine._conf * 100))
        self._conf_slider.valueChanged.connect(self._on_conf_changed)
        conf_row.addWidget(conf_lbl)
        conf_row.addWidget(self._conf_slider, stretch=1)
        conf_row.addWidget(self._conf_val_lbl)
        lay.addLayout(conf_row)

        return w

    def _build_session_section(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(6)

        def section_header(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                "color:#555; font-size:10px; letter-spacing:1px;"
                "border-top:1px solid #2a2a2a; padding-top:6px; margin-top:2px;"
            )
            return lbl

        def row(key, val_id):
            r = QHBoxLayout()
            k = QLabel(key)
            k.setStyleSheet("color:#666; font-size:12px;")
            v = QLabel("—")
            v.setObjectName(val_id)
            v.setStyleSheet("color:#aaa; font-size:11px; font-family:monospace;")
            v.setAlignment(Qt.AlignmentFlag.AlignRight)
            r.addWidget(k)
            r.addWidget(v, stretch=1)
            return r, v

        lay.addWidget(section_header("SESSION / BATCH INFO"))

        r1, self._lbl_batch   = row("Batch ID",      "lbl_batch")
        r2, self._lbl_start   = row("Started",        "lbl_start")
        r3, self._lbl_dur     = row("Duration",       "lbl_dur")
        r4, self._lbl_state   = row("Status",         "lbl_state")
        r5, self._lbl_latency = row("Last latency",   "lbl_lat")

        lay.addLayout(r1)
        lay.addLayout(r2)
        lay.addLayout(r3)
        lay.addLayout(r4)
        lay.addLayout(r5)

        self._lbl_state.setText("Ready")
        return w

    def _build_log_section(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        hdr = QWidget()
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(12, 8, 12, 6)
        t = QLabel("RECENT SCANS")
        t.setObjectName("SectionTitle")
        hdr_lay.addWidget(t)
        lay.addWidget(hdr)

        self._log_table = QTableWidget(0, 4)
        self._log_table.setHorizontalHeaderLabels(["ID", "Result", "Conf", "ms"])
        self._log_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed
        )
        self._log_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._log_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Fixed
        )
        self._log_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Fixed
        )
        self._log_table.setColumnWidth(0, 62)
        self._log_table.setColumnWidth(2, 50)
        self._log_table.setColumnWidth(3, 46)
        self._log_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._log_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._log_table.verticalHeader().setVisible(False)
        self._log_table.setShowGrid(False)
        lay.addWidget(self._log_table, stretch=1)

        return w

    # ------------------------------------------------------------------ #
    #  Hardware check                                                      #
    # ------------------------------------------------------------------ #
    def _perform_hw_check(self):
        monitor = HardwareMonitor()
        status  = monitor.check()

        if status.camera_ok:
            self._badge_cam.setText("● Camera")
            self._badge_cam.setObjectName("BadgeOk")
            self._badge_cam.style().unpolish(self._badge_cam)
            self._badge_cam.style().polish(self._badge_cam)
            self._worker.start_pipeline()
        else:
            self._badge_cam.setText("✕ No Camera")
            self._badge_cam.setObjectName("BadgeErr")
            self._badge_cam.style().unpolish(self._badge_cam)
            self._badge_cam.style().polish(self._badge_cam)
            self._btn_start.setEnabled(False)
            self._video_label.setText(
                "⚠  Camera not found.\nPlease connect a camera and restart the application."
            )
            logger.warning("Camera not found — scanning disabled.")

        if self._engine.is_demo_mode:
            self._badge_demo.setVisible(True)

    # ------------------------------------------------------------------ #
    #  Slots — video                                                       #
    # ------------------------------------------------------------------ #
    @pyqtSlot(np.ndarray, dict)
    def _on_frame(self, frame: np.ndarray, stats: dict):
        self._display_frame(frame)
        self._fps_label.setText(f"{stats.get('latency_ms', 0):.0f} ms")

    def _display_frame(self, frame: np.ndarray):
        rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pix  = QPixmap.fromImage(qimg)
        self._video_label.setPixmap(
            pix.scaled(
                self._video_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    @pyqtSlot(str)
    def _on_camera_error(self, msg: str):
        self._video_label.setText(f"⚠  {msg}")
        self._btn_start.setEnabled(False)
        self._badge_cam.setText("✕ Camera Error")
        self._badge_cam.setObjectName("BadgeErr")
        self._badge_cam.style().unpolish(self._badge_cam)
        self._badge_cam.style().polish(self._badge_cam)
        logger.error(f"Camera error: {msg}")

    # ------------------------------------------------------------------ #
    #  Slots — inspection                                                  #
    # ------------------------------------------------------------------ #
    @pyqtSlot(object, np.ndarray)
    def _on_inspection(self, result: InspectionResult, raw_frame: np.ndarray):
        saved = self._session_mgr.record_inspection(result, raw_frame)
        self._update_stats()
        self._add_log_row(saved)

    def _update_stats(self):
        s: SessionStats = self._session_mgr.stats
        self._card_total.set_value(s.total)
        self._card_healthy.set_value(s.healthy)
        self._card_bad.set_value(s.aflatoxin)
        self._card_ratio.set_value(f"{s.ratio}%")
        self._progress.setValue(int(s.ratio))

        try:
            fig_gram = float(self._fig_weight_input.text())
            if fig_gram > 0:
                total_gram = s.total * fig_gram
                if total_gram < 1000:
                    self._card_kg.set_value(f"{total_gram:.0f} g")
                else:
                    self._card_kg.set_value(f"{total_gram / 1000:.2f} kg")
            else:
                self._card_kg.set_value("—")
        except ValueError:
            self._card_kg.set_value("—")

    def _add_log_row(self, result: InspectionResult):
        row = 0
        self._log_table.insertRow(row)

        id_item = QTableWidgetItem(f"F{result.fig_id:04d}")
        id_item.setForeground(QColor("#666"))
        id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        is_bad   = result.decision == "Aflatoxin"
        res_item = QTableWidgetItem("Aflatoxin" if is_bad else "Healthy")
        res_item.setForeground(QColor("#E24B4A" if is_bad else "#1D9E75"))
        res_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        conf_item = QTableWidgetItem(f"{result.confidence:.0%}")
        conf_item.setForeground(QColor("#aaa"))
        conf_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        lat_item = QTableWidgetItem(f"{result.latency_ms:.0f}")
        lat_item.setForeground(QColor("#555"))
        lat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        self._log_table.setItem(row, 0, id_item)
        self._log_table.setItem(row, 1, res_item)
        self._log_table.setItem(row, 2, conf_item)
        self._log_table.setItem(row, 3, lat_item)
        self._log_table.setRowHeight(row, 28)

        if self._log_table.rowCount() > 100:
            self._log_table.removeRow(100)

        self._lbl_latency.setText(f"{result.latency_ms:.0f} ms")

    # ------------------------------------------------------------------ #
    #  Slots — buttons                                                     #
    # ------------------------------------------------------------------ #
    def _start_session(self):
        """Common session start logic."""
        batch = self._session_mgr.start_new_session()
        self._scan_start_time = datetime.now()
        self._worker.set_scanning(True)
        self._live_badge.setVisible(True)
        self._btn_start.setVisible(False)
        self._btn_stop.setVisible(True)
        self._btn_new_batch.setVisible(False)
        self._btn_export.setEnabled(False)
        self._lbl_batch.setText(batch)
        self._lbl_start.setText(self._scan_start_time.strftime("%H:%M:%S"))
        self._lbl_dur.setText("00:00:00")
        self._lbl_state.setText("Scanning…")
        self._dur_timer.start(1000)
        logger.info(f"Scanning started — {batch}")

    def _reset_ui_stats(self):
        """Reset stat cards and log table for a new batch."""
        self._card_total.set_value(0)
        self._card_healthy.set_value(0)
        self._card_bad.set_value(0)
        self._card_ratio.set_value("0%")
        self._card_kg.set_value("—")
        self._progress.setValue(0)
        self._log_table.setRowCount(0)

    @pyqtSlot()
    def _on_start_clicked(self):
        if not self._state_mgr.transition(AppState.SCANNING):
            return
        self._start_session()

    @pyqtSlot()
    def _update_duration(self):
        if not self._scan_start_time:
            return
        elapsed = int((datetime.now() - self._scan_start_time).total_seconds())
        h = elapsed // 3600
        m = (elapsed % 3600) // 60
        s = elapsed % 60
        self._lbl_dur.setText(f"{h:02d}:{m:02d}:{s:02d}")

    @pyqtSlot()
    def _on_stop_clicked(self):
        if not self._state_mgr.transition(AppState.READY):
            return
        self._dur_timer.stop()
        self._worker.set_scanning(False)
        self._session_mgr.end_session()
        self._live_badge.setVisible(False)
        self._btn_stop.setVisible(False)
        self._btn_start.setVisible(True)
        self._btn_new_batch.setVisible(True)
        self._btn_export.setEnabled(True)
        self._lbl_state.setText("Stopped")
        logger.info("Scanning stopped.")

    @pyqtSlot()
    def _on_new_batch_clicked(self):
        """Previous batch is kept in DB; UI resets and a new session opens."""
        if not self._state_mgr.transition(AppState.SCANNING):
            return
        self._reset_ui_stats()
        self._start_session()

    @pyqtSlot()
    def _on_export_clicked(self):
        path = self._session_mgr.export_csv()
        if path:
            QMessageBox.information(
                self, "Export Successful",
                f"CSV file saved:\n{path}"
            )
        else:
            QMessageBox.warning(
                self, "Error",
                "Export failed. No active session found."
            )

    @pyqtSlot(int)
    def _on_conf_changed(self, value: int):
        self._engine.set_conf_threshold(value / 100.0)
        self._conf_val_lbl.setText(f"{value}%")

    @pyqtSlot()
    def _open_db_viewer(self):
        viewer = DatabaseViewer(self._db, parent=self)
        viewer.exec()

    # ------------------------------------------------------------------ #
    #  Cleanup                                                             #
    # ------------------------------------------------------------------ #
    def closeEvent(self, event):
        logger.info("Application closing…")
        self._dur_timer.stop()
        self._state_mgr.transition(AppState.SHUTDOWN)
        if self._state_mgr.is_scanning():
            self._session_mgr.end_session()
        self._worker.stop()
        self._archiver.stop()
        self._db.close()
        event.accept()