import sys
from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class _Base(QObject):
    """No-op backend and shared base. update(status, title, length_us, volume);
    status is 'Playing' / 'Paused' / 'Stopped'. Callback names: play pause toggle stop
    next prev raise seek_rel(us) seek_abs(us) volume(0..1)."""
    _cmd = pyqtSignal(str, object)

    def __init__(self, callbacks, position_fn=lambda: 0, parent=None):
        super().__init__(parent)
        self._cbs, self._pos_fn = callbacks, position_fn
        self.status, self.title, self.length_us, self.volume = "Stopped", "", 0, 1.0
        self.can_next = self.can_prev = False
        self.ok = False
        self._cmd.connect(self._run)  # emitted from OS threads, runs on the Qt thread
        self._t = QTimer(self)
        self._t.setInterval(1000)
        self._t.timeout.connect(self._tick)
        self._t.start()

    def shutdown(self):
        self._t.stop()

    def _run(self, name, arg):
        fn = self._cbs.get(name)
        if fn:
            fn() if arg is None else fn(arg)

    def _pos(self):
        try:
            return int(self._pos_fn())
        except Exception:
            return 0

    def _tick(self):
        if self.status == "Playing":
            self._push()

    def _push(self):
        pass

    def update(self, status=None, title=None, length_us=None, volume=None, can_next=None, can_prev=None):
        if status is not None:
            self.status = status
        if title is not None:
            self.title = title
        if length_us is not None:
            self.length_us = int(length_us)
        if volume is not None:
            self.volume = volume
        if can_next is not None:
            self.can_next = can_next
        if can_prev is not None:
            self.can_prev = can_prev
        self._push()


def create(callbacks, position_fn=lambda: 0, name="hayate", parent=None):
    try:
        if sys.platform.startswith("linux"):
            from hayate.ui.mpris import Mpris
            return Mpris(callbacks, position_fn, name, parent)
        if sys.platform == "win32":
            from hayate.ui.media_win import WinMedia
            return WinMedia(callbacks, position_fn, parent)
        if sys.platform == "darwin":
            from hayate.ui.media_mac import MacMedia
            return MacMedia(callbacks, position_fn, parent)
    except Exception as e:
        print("media controls unavailable:", e, flush=True)
    return _Base(callbacks, position_fn, parent)
