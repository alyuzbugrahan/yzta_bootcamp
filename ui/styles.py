DARK_STYLE = """
/* ── Global ── */
QWidget {
    background-color: #1a1a1a;
    color: #e8e8e8;
    font-family: "Segoe UI", "SF Pro Display", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}

QMainWindow {
    background-color: #111111;
}

/* ── Title bar area ── */
#TitleBar {
    background-color: #161616;
    border-bottom: 1px solid #222;
}

#AppTitle {
    font-size: 14px;
    font-weight: 600;
    color: #f0f0f0;
    letter-spacing: 0.2px;
}

/* ── Panels ── */
#RightPanel {
    background-color: #161616;
    border-left: 1px solid #222;
}

#SectionTitle {
    font-size: 10px;
    color: #4a4a4a;
    letter-spacing: 1.2px;
    text-transform: uppercase;
}

/* ── Video frame ── */
#VideoFrame {
    background-color: #080808;
    border: 1px solid #222;
    border-radius: 10px;
}

#VideoLabel {
    background-color: #0a0a0a;
    color: #333;
    font-size: 14px;
}

/* ── Stat cards ── */
#StatCard {
    background-color: #1e1e1e;
    border-radius: 8px;
    border: 1px solid #272727;
}

#stat_label {
    font-size: 10px;
    color: #555;
    letter-spacing: 0.8px;
    text-transform: uppercase;
}

/* ── Buttons ── */
QPushButton {
    border: 1px solid #303030;
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 500;
    background-color: #222;
    color: #ccc;
}

QPushButton:hover {
    background-color: #2a2a2a;
    border-color: #444;
}

QPushButton:pressed {
    background-color: #1a1a1a;
}

QPushButton:disabled {
    color: #333;
    border-color: #222;
}

#BtnStart {
    background-color: #1D9E75;
    color: #ffffff;
    border-color: #1D9E75;
    font-weight: 600;
    font-size: 13px;
}
#BtnStart:hover  { background-color: #17875f; }
#BtnStart:pressed{ background-color: #126b4c; }

#BtnStop {
    background-color: #2a1010;
    color: #e24b4a;
    border-color: #4a1818;
}
#BtnStop:hover { background-color: #361414; }

#BtnExport {
    background-color: #1e1e1e;
    color: #888;
    border-color: #2a2a2a;
}
#BtnExport:hover { background-color: #252525; color: #bbb; }

/* ── Slider ── */
QSlider::groove:horizontal {
    height: 3px;
    background: #2a2a2a;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #1D9E75;
    width: 13px;
    height: 13px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: #1D9E75;
    border-radius: 2px;
}

/* ── Table / Log ── */
QTableWidget {
    background-color: #111;
    border: none;
    gridline-color: #1e1e1e;
    selection-background-color: #1e2e28;
    font-size: 12px;
    outline: none;
}

QTableWidget::item {
    padding: 4px 8px;
    border-bottom: 1px solid #1a1a1a;
    color: #ccc;
}

QTableWidget::item:selected {
    background: #1e2e28;
    color: #e8e8e8;
}

QHeaderView::section {
    background-color: #161616;
    color: #4a4a4a;
    font-size: 10px;
    padding: 5px 8px;
    border: none;
    border-bottom: 1px solid #222;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}

/* ── Separator ── */
#Separator {
    color: #222;
    background-color: #222;
    border: none;
    max-height: 1px;
}

/* ── Status badges ── */
#BadgeOk {
    background-color: #0a2018;
    color: #1D9E75;
    border-radius: 10px;
    padding: 2px 9px;
    font-size: 11px;
}
#BadgeWarn {
    background-color: #251800;
    color: #d4880a;
    border-radius: 10px;
    padding: 2px 9px;
    font-size: 11px;
}
#BadgeErr {
    background-color: #220a0a;
    color: #e24b4a;
    border-radius: 10px;
    padding: 2px 9px;
    font-size: 11px;
}

/* ── Scrollbar ── */
QScrollBar:vertical {
    background: #111;
    width: 5px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #2e2e2e;
    border-radius: 3px;
    min-height: 20px;
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical { height: 0; }

/* ── Tooltip ── */
QToolTip {
    background-color: #1e1e1e;
    color: #ddd;
    border: 1px solid #333;
    border-radius: 5px;
    padding: 5px 10px;
    font-size: 12px;
}

/* ── Progress bar ── */
QProgressBar {
    background-color: #1e1e1e;
    border: none;
    border-radius: 2px;
    height: 4px;
    font-size: 0px;
}
QProgressBar::chunk {
    background-color: #e24b4a;
    border-radius: 2px;
}

/* ── SpinBox / LineEdit ── */
QSpinBox, QDoubleSpinBox, QLineEdit {
    background-color: #1e1e1e;
    border: 1px solid #2e2e2e;
    border-radius: 5px;
    padding: 4px 8px;
    color: #ddd;
}
QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
    border-color: #1D9E75;
}

/* ── ComboBox ── */
QComboBox {
    background-color: #1e1e1e;
    border: 1px solid #2e2e2e;
    border-radius: 5px;
    padding: 4px 8px;
    color: #ddd;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background-color: #1e1e1e;
    border: 1px solid #2e2e2e;
    selection-background-color: #252525;
    color: #ddd;
}

# Mevcut BtnStart stilinin yanına
QPushButton#BtnNewBatch {
    background: #0d2030;
    color: #4a9edd;
    border: 1px solid #1a4a6a;
    border-radius: 6px;
}
QPushButton#BtnNewBatch:hover {
    background: #142840;
    border-color: #2a6a9a;
}

/* ── MessageBox ── */
QMessageBox {
    background-color: #161616;
}
QMessageBox QLabel {
    color: #ddd;
}

/* ── Dialog ── */
QDialog {
    background-color: #141414;
}

/* ── SplashScreen ── */
QSplashScreen {
    background-color: #141414;
}
"""
