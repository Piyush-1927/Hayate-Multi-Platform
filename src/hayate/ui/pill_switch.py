from PyQt6.QtCore import QEasingCurve, QTimer, QVariantAnimation, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QPushButton

_ACTIVE = """
    QPushButton { background-color: transparent; color: #090a0f; font-weight: 800;
                  font-size: 11px; border-radius: 6px; border: none; padding: 0 12px; }
"""
_INACTIVE = """
    QPushButton { background-color: transparent; color: #94a3b8; font-weight: 600;
                  font-size: 11px; border-radius: 6px; border: none; padding: 0 12px; }
    QPushButton:hover { color: #ffffff; }
"""


class PillSwitch(QFrame):
    """Segmented switch with a gliding pill; same look and motion as the schedule page switcher.
    `changed(key)` fires on user clicks only; set_current() never emits."""

    changed = pyqtSignal(str)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.setObjectName("pillSwitch")
        self.setStyleSheet("QFrame#pillSwitch { background-color: #212130; border-radius: 8px; border: 1.5px solid #3d3d52; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(4)
        self._btns = {}
        for key, label in options:
            b = QPushButton(label)
            b.setFixedHeight(30)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, k=key: self._on_click(k))
            lay.addWidget(b)
            self._btns[key] = b
        self._cur = options[0][0]
        self._pill = QFrame(self)
        self._pill.setObjectName("pill")
        self._pill.setStyleSheet("QFrame#pill { background-color: #38bdf8; border: none; border-radius: 6px; }")
        self._pill.lower()
        self._anims = []
        self._pl = self._pr = 0.0
        self._top = 0
        self._h = 30
        self._restyle()

    def current(self):
        return self._cur

    def set_current(self, key, animate=True):
        if key not in self._btns:
            return
        self._cur = key
        self._restyle()
        self._glide(animate)

    def _on_click(self, key):
        if key != self._cur:
            self.set_current(key)
            self.changed.emit(key)

    def _restyle(self):
        for k, b in self._btns.items():
            b.setStyleSheet(_ACTIVE if k == self._cur else _INACTIVE)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._resync)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._resync)

    def _resync(self):
        self._glide(any(a.state() == QVariantAnimation.State.Running for a in self._anims))

    def _apply(self):
        self._pill.setGeometry(round(self._pl), self._top, max(1, round(self._pr - self._pl)), self._h)

    def _glide(self, animate):
        self.layout().activate()
        g = self._btns[self._cur].geometry()
        tl, tr = float(g.left()), float(g.right() + 1)
        self._top, self._h = g.top(), g.height()
        for a in self._anims:
            a.stop()
        self._anims = []
        if not animate or not self.isVisible() or self._pr <= self._pl:
            self._pl, self._pr = tl, tr
            self._apply()
            return
        going_right = tl >= self._pl

        def edge(attr, end, ms):
            a = QVariantAnimation()
            a.setStartValue(getattr(self, attr))
            a.setEndValue(end)
            a.setDuration(ms)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.valueChanged.connect(lambda v, at=attr: (setattr(self, at, float(v)), self._apply()))
            a.start()
            self._anims.append(a)

        edge("_pr", tr, 220 if going_right else 380)  # leading edge fast
        edge("_pl", tl, 380 if going_right else 220)  # trailing edge lags
