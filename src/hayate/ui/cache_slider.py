from PyQt6.QtCore import Qt, QEvent, pyqtSignal
from PyQt6.QtWidgets import QSlider


class ClickOnlySlider(QSlider):
    """Changes only by click or press-hold-drag (also double-tap-hold-drag).
    Ignores wheel, keyboard and trackpad/touch gestures; snaps to `step`."""
    committed = pyqtSignal(int)  # emitted on release

    def __init__(self, step=25, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._step, self._drag = step, False
        self.setSingleStep(step); self.setPageStep(step)

    def _at(self, x):
        w = max(1, self.width()); lo, hi = self.minimum(), self.maximum()
        v = lo + (hi - lo) * min(max(x, 0), w) / w
        return max(lo, min(hi, int(round(v / self._step)) * self._step))

    def wheelEvent(self, e): e.ignore()
    def keyPressEvent(self, e): e.ignore()

    def event(self, e):
        if e.type() in (QEvent.Type.Gesture, QEvent.Type.NativeGesture):
            e.ignore(); return True
        return super().event(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = True; self.setValue(self._at(e.position().x())); e.accept()

    mouseDoubleClickEvent = mousePressEvent

    def mouseMoveEvent(self, e):
        if self._drag:
            self.setValue(self._at(e.position().x())); e.accept()

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False; self.committed.emit(self.value()); e.accept()
