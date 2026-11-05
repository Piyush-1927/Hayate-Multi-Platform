"""OpenGL video surface for libmpv's render API (native Wayland, no --wid)."""
import ctypes

from PyQt6.QtCore import Qt, QPoint, pyqtSignal
from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtOpenGLWidgets import QOpenGLWidget


class GLVideoSurface(QOpenGLWidget):
    doubleClickedCenter = pyqtSignal()
    _redraw = pyqtSignal()  # mpv's update callback fires on an mpv thread

    def __init__(self, parent=None):
        super().__init__(parent)
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        self.setFormat(fmt)
        self.setMouseTracking(True)
        self._mpv = None
        self._ctx = None
        self._gpa = None
        self._have_frame = False
        self._redraw.connect(self.update, Qt.ConnectionType.QueuedConnection)

    # --- called by MpvPlayerAdapter ---
    def attach(self, mpv_player):
        self._mpv = mpv_player
        if self.isValid() and self._ctx is None:
            self.makeCurrent()
            try:
                self._create_ctx()
            finally:
                self.doneCurrent()
            self.update()

    def detach(self):
        """Free the render context (GL context current) BEFORE mpv terminates."""
        ctx, self._ctx = self._ctx, None
        self._mpv = None
        self._have_frame = False
        if ctx is None:
            return
        self.makeCurrent()
        try:
            ctx.free()
        finally:
            self.doneCurrent()

    # --- GL plumbing ---
    def _get_proc_address(self, _, name):
        try:
            addr = self.context().getProcAddress(name)
            return ctypes.cast(int(addr), ctypes.c_void_p).value if addr else 0
        except Exception:
            return 0

    def _on_mpv_update(self):
        self._have_frame = True
        try:
            self._redraw.emit()
        except RuntimeError:
            pass

    def _create_ctx(self):
        from mpv import MpvRenderContext, MpvGlGetProcAddressFn
        self._gpa = MpvGlGetProcAddressFn(self._get_proc_address)
        self._ctx = MpvRenderContext(
            self._mpv, "opengl",
            opengl_init_params={"get_proc_address": self._gpa},
        )
        self._have_frame = False
        self._ctx.update_cb = self._on_mpv_update

    def initializeGL(self):
        if self._mpv is not None and self._ctx is None:
            self._create_ctx()

    def paintGL(self):
        if self._ctx is None or self._mpv is None or not self._have_frame:
            from PyQt6.QtGui import QPainter, QColor
            _p = QPainter(self)
            _p.fillRect(self.rect(), QColor(0, 0, 0))
            _p.end()
            return
        r = self.devicePixelRatioF()
        self._ctx.render(
            flip_y=True,
            opengl_fbo={"w": int(self.width() * r), "h": int(self.height() * r),
                        "fbo": self.defaultFramebufferObject()},
        )

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            c = QPoint(self.width() // 2, self.height() // 2)
            d2 = (event.position().x() - c.x()) ** 2 + (event.position().y() - c.y()) ** 2
            if d2 <= 160 ** 2:
                self.doubleClickedCenter.emit()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)
