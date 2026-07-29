from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QFrame
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont


class StatCard(QWidget):
    """Tek sayısal istatistiği gösteren kart."""

    def __init__(self, label: str, color: str = "#e0e0e0", parent=None):
        super().__init__(parent)
        self._color = color
        self.setObjectName("StatCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        self._label = QLabel(label)
        self._label.setObjectName("stat_label")

        self._value = QLabel("0")
        self._value.setObjectName("stat_value")
        font = QFont()
        font.setPointSize(22)
        font.setWeight(QFont.Weight.Medium)
        self._value.setFont(font)
        self._value.setStyleSheet(f"color: {color};")

        layout.addWidget(self._label)
        layout.addWidget(self._value)

    def set_value(self, v):
        self._value.setText(str(v))


class Separator(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setObjectName("Separator")
