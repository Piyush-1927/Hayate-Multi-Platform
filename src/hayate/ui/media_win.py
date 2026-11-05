from datetime import timedelta
from winrt.windows.media import (MediaPlaybackStatus as S, MediaPlaybackType,
                                 SystemMediaTransportControlsButton as B,
                                 SystemMediaTransportControlsTimelineProperties as TL)
from winrt.windows.media.playback import MediaPlayer
from hayate.ui.media_controls import _Base

_BTN = {B.PLAY: "toggle", B.PAUSE: "toggle", B.STOP: "stop", B.NEXT: "next", B.PREVIOUS: "prev"}
_ST = {"Playing": S.PLAYING, "Paused": S.PAUSED, "Stopped": S.STOPPED}

# Windows gives one SMTC session per app: every player shares it and the
# most recently playing one owns it.
_hub = None
_clients = []
_active = None


def _target():
    if _active in _clients:
        return _active
    return _clients[-1] if _clients else None


def _on_btn(sender, args):
    t, name = _target(), _BTN.get(args.button)
    if t and name:
        t._cmd.emit(name, None)


def _on_seek(sender, args):
    t = _target()
    if t:
        t._cmd.emit("seek_abs", int(args.requested_playback_position.total_seconds() * 1e6))


def _get_hub():
    global _hub
    if _hub is None:
        mp = MediaPlayer()
        mp.command_manager.is_enabled = False  # we drive SMTC ourselves
        c = mp.system_media_transport_controls
        c.is_enabled = True
        c.is_play_enabled = c.is_pause_enabled = c.is_stop_enabled = True
        c.add_button_pressed(_on_btn)
        c.add_playback_position_change_requested(_on_seek)
        _hub = (mp, c)
    return _hub


class WinMedia(_Base):
    def __init__(self, callbacks, position_fn=lambda: 0, parent=None):
        super().__init__(callbacks, position_fn, parent)
        self._mp, self._c = _get_hub()
        self._meta = None
        _clients.append(self)
        self.ok = True

    def shutdown(self):
        global _active
        if self in _clients:
            _clients.remove(self)
        if _active is self:
            _active = None
            nxt = next((c for c in _clients if c.status != "Stopped"), None) or (_clients[-1] if _clients else None)
            if nxt:
                _active = nxt
                nxt._meta = None
                nxt._write()
        super().shutdown()

    def _push(self):
        global _active
        if _active is not self and (self.status == "Playing" or _active not in _clients):
            _active = self
            self._meta = None
        elif _active is self and self.status == "Stopped":
            other = next((c for c in _clients if c is not self and c.status != "Stopped"), None)
            if other:
                _active = other
                other._meta = None
                other._write()
                return
        if _active is self:
            self._write()

    def _write(self):
        try:
            c = self._c
            c.playback_status = _ST.get(self.status, S.STOPPED)
            c.is_next_enabled, c.is_previous_enabled = self.can_next, self.can_prev
            meta = (self.title, self.length_us)
            if meta != self._meta:
                self._meta = meta
                u = c.display_updater
                u.type = MediaPlaybackType.VIDEO
                u.video_properties.title = self.title or "Hayate"
                u.update()
            if self.length_us:
                tl = TL()
                end = timedelta(microseconds=self.length_us)
                tl.start_time = tl.min_seek_time = timedelta(0)
                tl.end_time = tl.max_seek_time = end
                tl.position = min(timedelta(microseconds=self._pos()), end)
                c.update_timeline_properties(tl)
        except Exception as e:
            print("SMTC update failed:", e, flush=True)
