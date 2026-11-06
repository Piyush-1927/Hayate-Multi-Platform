import time
from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, QRect, QRectF, QPoint, Qt
from PyQt6.QtGui import QPainter, QColor
from PyQt6.QtWidgets import QApplication, QScrollBar, QWidget, QAbstractScrollArea

IDLE_MS = 900
FADE_IN_MS = 120
FADE_OUT_MS = 400
TICK_MS = 40
SCAN_MS = 1000
BASE_ALPHA = 0.40
HOT_ALPHA = 0.70
THICK = 6
MIN_LEN = 30

BASE_QSS = """
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal,
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover,
QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed { background: transparent; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""


class _Overlay(QWidget):
    def __init__(self, area, sb):
        super().__init__(area)
        self.sb = sb
        self.alpha = 0.0
        self.hot = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.hide()

    def paintEvent(self, ev):
        if self.alpha <= 0.01 or sip.isdeleted(self.sb):
            return
        sb = self.sb
        lo, hi, page = sb.minimum(), sb.maximum(), sb.pageStep()
        span = hi - lo
        if span <= 0:
            return
        vert = sb.orientation() == Qt.Orientation.Vertical
        track = (self.height() if vert else self.width()) - 4
        if track <= 0:
            return
        length = min(max(MIN_LEN, track * page / float(span + page)), track)
        pos = 2 + (sb.value() - lo) / float(span) * (track - length)
        a = (HOT_ALPHA if self.hot else BASE_ALPHA) * self.alpha
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, int(255 * a)))
        if vert:
            r = QRectF((self.width() - THICK) / 2.0, pos, THICK, length)
        else:
            r = QRectF(pos, (self.height() - THICK) / 2.0, length, THICK)
        p.drawRoundedRect(r, THICK / 2.0, THICK / 2.0)


class _Entry:
    __slots__ = ("sb", "area", "ov", "state", "active_at")

    def __init__(self, sb, area):
        self.sb = sb
        self.area = area
        self.ov = _Overlay(area, sb)
        self.state = (sb.value(), sb.maximum(), sb.pageStep())
        self.active_at = 0.0


class _Manager(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.entries = {}
        self.last = time.monotonic()
        self.since_scan = SCAN_MS
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        app.aboutToQuit.connect(self.shutdown)

    def shutdown(self):
        self.timer.stop()
        self.entries.clear()

    def _wake(self, *_):
        if self.timer.interval() != TICK_MS:
            self.timer.setInterval(TICK_MS)

    def _scan(self):
        for top in self.app.topLevelWidgets():
            for sb in top.findChildren(QScrollBar):
                if sb in self.entries:
                    continue
                area = sb.parent()
                while area is not None and not isinstance(area, QAbstractScrollArea):
                    area = area.parent()
                if area is not None:
                    self.entries[sb] = _Entry(sb, area)
                    sb.valueChanged.connect(self._wake)
                    sb.rangeChanged.connect(self._wake)

    def _tick(self):
        now = time.monotonic()
        dt = (now - self.last) * 1000.0
        self.last = now
        self.since_scan += dt
        if self.since_scan >= SCAN_MS:
            self.since_scan = 0
            self._scan()

        dead = []
        busy = False
        for sb, e in self.entries.items():
            if sip.isdeleted(sb) or sip.isdeleted(e.area):
                dead.append(sb)
                continue
            ov = e.ov
            if not sb.isVisible() or not e.area.isVisible():
                if ov.alpha:
                    ov.alpha = 0.0
                    ov.hide()
                continue

            st = (sb.value(), sb.maximum(), sb.pageStep())
            if st != e.state:
                e.state = st
                e.active_at = now
            hot = sb.underMouse() or sb.isSliderDown()
            if hot:
                e.active_at = now

            want = sb.maximum() > sb.minimum() and (now - e.active_at) * 1000.0 < IDLE_MS
            if not want and ov.alpha == 0.0:
                continue

            busy = True
            rect = QRect(sb.mapTo(e.area, QPoint(0, 0)), sb.size())
            if ov.geometry() != rect:
                ov.setGeometry(rect)
            ov.hot = hot
            if want:
                ov.alpha = min(1.0, ov.alpha + dt / FADE_IN_MS)
            else:
                ov.alpha = max(0.0, ov.alpha - dt / FADE_OUT_MS)
            if ov.alpha > 0.0:
                if not ov.isVisible():
                    ov.show()
                ov.raise_()
                ov.update()
            else:
                ov.hide()
        for sb in dead:
            self.entries.pop(sb, None)
        iv = TICK_MS if busy else 250
        if self.timer.interval() != iv:
            self.timer.setInterval(iv)


def install(app):
    """Call once after QApplication is created."""
    app._scrollbar_autohide = _Manager(app)
