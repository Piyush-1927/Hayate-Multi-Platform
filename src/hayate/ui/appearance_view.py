from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollArea, QFrame
from hayate.core.theme import ThemeSettingsCard

class AppearanceView(QWidget):
    back_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("pageroot")
        from PyQt6.QtCore import Qt as _Qt
        from hayate.core import theme as _thm
        self.setAttribute(_Qt.WidgetAttribute.WA_StyledBackground, True)
        def _bg():
            try: _thm._orig_set(self, "#pageroot{background:%s;}" % _thm.theme.tokens()["app_background"])
            except RuntimeError: pass
        _thm.theme.changed.connect(_bg); _bg()
        root = QVBoxLayout(self); root.setContentsMargins(40, 24, 40, 24)
        top = QHBoxLayout(); b = QPushButton("\u2190  Back"); b.clicked.connect(self.back_requested.emit)
        top.addWidget(b); top.addSpacing(12); top.addWidget(QLabel("<span style='font-size:20px;font-weight:800'>Appearance</span>")); top.addStretch()
        root.addLayout(top)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setStyleSheet("QScrollArea{background:transparent;border:none;}"); sc.viewport().setAutoFillBackground(False)
        w = QWidget(); w.setObjectName("apv"); w.setStyleSheet("#apv{background:transparent;}")
        wl = QVBoxLayout(w); wl.setContentsMargins(0, 16, 0, 0); wl.addWidget(ThemeSettingsCard()); wl.addStretch()
        sc.setWidget(w); root.addWidget(sc, 1)
