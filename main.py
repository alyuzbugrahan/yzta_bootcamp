import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont

from ui.main_window import MainWindow
from utils.logger import logger


def _make_splash(progress: float = 0.0, message: str = "Initializing…",
                 w: int = 600, h: int = 400) -> QPixmap:
    pix = QPixmap(w, h)
    pix.fill(QColor("#0f0f0f"))

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    # ── Dış çerçeve ──
    p.setPen(QColor("#222222"))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(1, 1, w - 2, h - 2, 14, 14)

    # ── Üst mor aksent ──
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#7c3aed"))
    p.drawRoundedRect(0, 0, w, 4, 2, 2)

    # ── Logo ──
    logo_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "assets", "figion.png"
    )
    logo_h    = 280
    logo_y    = 30
    if os.path.exists(logo_path):
        logo = QPixmap(logo_path).scaledToHeight(
            logo_h, Qt.TransformationMode.SmoothTransformation
        )
        p.drawPixmap((w - logo.width()) // 2, logo_y, logo)
    else:
        cx, cy, r = w // 2, logo_y + logo_h // 2, 56
        p.setBrush(QColor("#1a1230"))
        p.setPen(QColor("#7c3aed"))
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)
        f0 = QFont("Segoe UI", 38, QFont.Weight.Bold)
        p.setFont(f0)
        p.setPen(QColor("#7c3aed"))
        p.drawText(cx - 14, cy + 15, "F")

    # ── Uygulama adı ──
    """f1 = QFont("Segoe UI", 26, QFont.Weight.Light)
    p.setFont(f1)
    p.setPen(QColor("#f0f0f0"))
    p.drawText(0, logo_y + logo_h + 20, w, 38,
               Qt.AlignmentFlag.AlignHCenter, "Figion")""" 

    # ── Alt başlık ──
    f2 = QFont("Segoe UI", 10)
    p.setFont(f2)
    p.setPen(QColor("#4a4a4a"))
    # Yazıyı daha yukarı almak için eksi değer kullanıyoruz (-15 yapabilirsiniz)
    p.drawText(0, logo_y + logo_h - 15, w, 24,
               Qt.AlignmentFlag.AlignHCenter,
               "Aflatoxin Detection System  ·  v1.0")

    # ── Progress bar ── 110 dan 60 a yaklaştırdık 
    bar_x  = w // 2 - 120
    # Barı yukarı çekmek için +40 yerine +10 yapıyoruz
    bar_y  = logo_y + logo_h + 5
    bar_w  = 240
    bar_h  = 4

    # Arka plan
    p.setBrush(QColor("#1e1e1e"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(bar_x, bar_y, bar_w, bar_h, 2, 2)

    # Dolgu
    fill_w = int(bar_w * min(progress, 1.0))
    if fill_w > 0:
        p.setBrush(QColor("#7c3aed"))
        p.drawRoundedRect(bar_x, bar_y, fill_w, bar_h, 2, 2)
        # Parlayan uç
        p.setBrush(QColor("#a78bfa"))
        p.drawEllipse(bar_x + fill_w - 4, bar_y - 3, 8, 8)

    # Yüzde
    f3 = QFont("Segoe UI", 9)
    p.setFont(f3)
    p.setPen(QColor("#7c3aed"))
    p.drawText(bar_x + bar_w + 10, bar_y + bar_h + 5,
               f"{int(progress * 100)}%")

    # ── Loading mesajı ──
    f4 = QFont("Segoe UI", 9)
    p.setFont(f4)
    p.setPen(QColor("#3a3a3a"))
    p.drawText(0, bar_y + 15, w, 22,
               Qt.AlignmentFlag.AlignHCenter, message)

    # ── Alt not ──
    f5 = QFont("Segoe UI", 8)
    p.setFont(f5)
    p.setPen(QColor("#252525"))
    p.drawText(0, h - 20, w, 18,
               Qt.AlignmentFlag.AlignHCenter,
               "Developed by Ayça Uçankale · © 2026")

    p.end()
    return pix

def main():
    for d in ("data/images", "data/logs", "data/exports", "models"):
        os.makedirs(d, exist_ok=True)

    app = QApplication(sys.argv)
    app.setApplicationName("Figion")
    app.setApplicationVersion("1.0.0")

    from PyQt6.QtGui import QPalette
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor("#1a1a1a"))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor("#e8e8e8"))
    palette.setColor(QPalette.ColorRole.Base,            QColor("#141414"))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor("#1e1e1e"))
    palette.setColor(QPalette.ColorRole.Text,            QColor("#e8e8e8"))
    palette.setColor(QPalette.ColorRole.Button,          QColor("#252525"))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor("#e8e8e8"))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor("#7c3aed"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    splash = QSplashScreen(
        _make_splash(0.0, "Starting…"),
        Qt.WindowType.WindowStaysOnTopHint
    )
    splash.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    splash.show()
    app.processEvents()

    steps = [
        (0.15, "Initializing logger…",     0.15),
        (0.32, "Loading configuration…",   0.20),
        (0.52, "Connecting to database…",  0.25),
        (0.74, "Loading AI model…",        0.50),
        (0.90, "Starting camera…",         0.20),
        (1.00, "Ready.",                   0.15),
    ]

    for progress, message, delay in steps:
        splash.setPixmap(_make_splash(progress, message))
        app.processEvents()
        time.sleep(delay)

    logger.info("Figion starting…")

    window = MainWindow()
    splash.finish(window)
    window.showMaximized()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()