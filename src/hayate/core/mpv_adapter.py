"""Qt-compatible adapter around python-mpv."""

import locale
import threading

from PyQt6 import QtCore
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt6.QtMultimedia import QMediaPlayer

from hayate.core.playback_platform import PlaybackPlatform


class MpvAudioOutput:
    """Small compatibility layer for the QAudioOutput methods used by PlayerView."""

    def __init__(self, player):
        self._player = player
        self._muted = False
        self._volume = 1.0

    def isMuted(self):
        return self._muted

    def setMuted(self, muted):
        self._muted = bool(muted)
        self._apply_volume()

    def setVolume(self, volume):
        self._volume = max(0.0, min(1.0, float(volume)))
        self._apply_volume()

    def volume(self):
        return self._volume

    def _apply_volume(self):
        self._player.mute = self._muted
        self._player.volume = self._volume * 100.0


class MpvPlayerAdapter(QObject):
    """Expose the subset of QMediaPlayer's interface used by PlayerView."""

    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    playbackStateChanged = pyqtSignal(object)
    errorOccurred = pyqtSignal(str)

    # mpv callbacks may run on native threads. Relay their data through Qt.
    _property_update = pyqtSignal(str, object)

    def __init__(self, platform: PlaybackPlatform, native_id: int, parent=None):
        super().__init__(parent)

        if not platform.supported:
            raise RuntimeError(platform.reason or "Native video embedding is unsupported.")

        if platform.embedding not in ("hwnd", "x11"):
            raise RuntimeError(
                f"Embedding backend '{platform.embedding}' is not implemented."
            )

        if not native_id:
            raise RuntimeError("The native video surface has no valid window ID.")

        # libmpv requires LC_NUMERIC=C. Preserve other locale categories.
        try:
            locale.setlocale(locale.LC_NUMERIC, "C")
        except locale.Error as exc:
            raise RuntimeError(f"Could not set LC_NUMERIC=C: {exc}") from exc

        try:
            import mpv
        except (ImportError, OSError) as exc:
            raise RuntimeError(
                "Could not import python-mpv or load the native libmpv library. "
                "Install python-mpv and ensure the matching libmpv shared "
                "library is installed and discoverable."
            ) from exc

        self._lock = threading.RLock()
        self._source = None
        self._position_ms = 0
        self._duration_ms = 0
        self._closed = False
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self._paused = False
        self._core_idle = True

        self._property_update.connect(
            self._on_property_update,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )

        native_id = int(native_id)
        if platform.embedding == "hwnd":
            # mpv's Windows wid option expects an unsigned 32-bit HWND value.
            native_id &= 0xFFFFFFFF

        options = {
            "wid": str(native_id),
            "vo": "gpu",
            "gpu_context": "x11egl" if platform.embedding == "x11" else "auto",
            "keep_open": "yes",
            "force_window": "yes",
            "input_default_bindings": False,
            "input_vo_keyboard": False,
            "osc": False,
            "terminal": False,
        }

        try:
            from hayate.core import net as _net
            options.update(_net.mpv_options())
        except OSError as exc:  # proxy enabled but unusable: fail closed, never play direct
            raise RuntimeError(f"Network proxy error: {exc}") from exc

        try:
            self._mpv = mpv.MPV(**options)
            self._mpv.observe_property("time-pos", self._observe_property)
            self._mpv.observe_property("duration", self._observe_property)
            self._mpv.observe_property("pause", self._observe_property)
            self._mpv.observe_property("core-idle", self._observe_property)
        except Exception as exc:
            try:
                self._mpv.terminate()
            except Exception:
                pass
            raise RuntimeError(f"Could not initialize embedded libmpv: {exc}") from exc

        self.audio_output = MpvAudioOutput(self._mpv)

    def _observe_property(self, name, value):
        try:
            self._property_update.emit(str(name), value)
        except RuntimeError:
            # Qt object may already be shutting down.
            pass

    @pyqtSlot(str, object)
    def _on_property_update(self, name, value):
        if self._closed:
            return

        if name == "time-pos" and value is not None:
            self._position_ms = max(0, int(float(value) * 1000))
            self.positionChanged.emit(self._position_ms)

        elif name == "duration" and value is not None:
            self._duration_ms = max(0, int(float(value) * 1000))
            self.durationChanged.emit(self._duration_ms)

        elif name == "pause":
            if value is not None:
                self._paused = bool(value)
            self._refresh_playback_state()

        elif name == "core-idle":
            if value is not None:
                self._core_idle = bool(value)
            self._refresh_playback_state()

    def _refresh_playback_state(self):
        if self._closed:
            return

        if self._paused:
            state = QMediaPlayer.PlaybackState.PausedState
        elif self._core_idle:
            state = QMediaPlayer.PlaybackState.StoppedState
        else:
            state = QMediaPlayer.PlaybackState.PlayingState

        if state != self._state:
            self._state = state
            self.playbackStateChanged.emit(state)

    def setSource(self, url, referer="", headers=None, subtitles=()):
        self._referer = referer or ""
        self._headers = dict(headers or {})
        self._subs = [s for s in (subtitles or ()) if s]
        self._source = url.toEncoded().data().decode("ascii") if hasattr(url, "toString") else str(url)
        self._position_ms = 0
        self._duration_ms = 0

    def setStartPosition(self, seconds):
        """One-shot: the next setSource()/play() starts at this position."""
        self._start_s = max(0.0, float(seconds or 0))

    def _apply_stream_options(self):
        """Per-stream Referer/headers (set before loadfile) and external subtitles (added once loaded)."""
        try:
            s = getattr(self, "_start_s", 0.0)
            self._mpv["start"] = f"{s:.1f}" if s > 0 else "none"
            self._start_s = 0.0
        except Exception:
            pass
        try:
            self._mpv["referrer"] = getattr(self, "_referer", "") or ""
        except Exception:
            pass
        try:
            hdr = [f"{k}: {v}" for k, v in (getattr(self, "_headers", None) or {}).items()]
            self._mpv["http-header-fields"] = hdr
        except Exception:
            pass
        self._pending_subs = list(getattr(self, "_subs", []))
        if not getattr(self, "_sub_hook", False):
            self._sub_hook = True

            @self._mpv.event_callback("file-loaded")
            def _on_loaded(_event):
                subs, self._pending_subs = self._pending_subs, []
                for u in subs:
                    try:
                        self._mpv.command("sub-add", u, "select")
                    except Exception:
                        pass

    def play(self):
        if self._closed:
            return

        try:
            if self._source:
                # A newly selected source takes priority over EOF replay.
                source = self._source
                self._source = None
                self._mpv.pause = False
                self._apply_stream_options()
                self._mpv.loadfile(source, mode="replace")
            elif bool(self._mpv.eof_reached):
                # With keep_open=yes, seeking/unpausing alone didn't recover
                # at EOF in the installed mpv build. Reload the active source.
                try:
                    self._mpv["start"] = "none"  # EOF replay must start from 0
                except Exception:
                    pass
                source = self._mpv.path
                if source:
                    self._mpv.loadfile(source, mode="replace")
                self._mpv.pause = False
            else:
                self._mpv.pause = False
        except Exception as exc:
            self.errorOccurred.emit(str(exc))

    def pause(self):
        if self._closed:
            return
        try:
            self._mpv.pause = True
        except Exception as exc:
            self.errorOccurred.emit(str(exc))
            return

        self._state = QMediaPlayer.PlaybackState.PausedState
        self.playbackStateChanged.emit(self._state)

    def stop(self):
        if self._closed:
            return
        try:
            self._mpv.command("stop")
        except Exception as exc:
            self.errorOccurred.emit(str(exc))
        self._position_ms = 0
        self._paused = False
        self._core_idle = True
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self.positionChanged.emit(0)
        self.playbackStateChanged.emit(self._state)

    def setPosition(self, milliseconds):
        if self._closed:
            return
        try:
            self._mpv.seek(
                max(0, int(milliseconds)) / 1000.0,
                reference="absolute",
                precision="exact",
            )
        except Exception as exc:
            self.errorOccurred.emit(str(exc))

    def seekRelative(self, delta_ms):
        if self._closed:
            return
        try:
            current = self._mpv.time_pos
            if current is None:
                current = self._position_ms / 1000.0
            duration = self._mpv.duration
            target = max(0.0, float(current) + int(delta_ms) / 1000.0)
            if duration is not None:
                target = min(float(duration), target)
            self._mpv.seek(target, reference="absolute", precision="exact")
        except Exception as exc:
            self.errorOccurred.emit(str(exc))

    def setSpeed(self, rate):
        if self._closed:
            return
        try:
            self._mpv.speed = float(rate)
        except Exception as exc:
            self.errorOccurred.emit(str(exc))

    def speed(self):
        try:
            return float(self._mpv.speed)
        except Exception:
            return 1.0

    def subtitleTracks(self):
        """mpv subtitle tracks: dicts with id, title, lang, selected."""
        if self._closed:
            return []
        try:
            return [t for t in (self._mpv.track_list or []) if t.get("type") == "sub"]
        except Exception:
            return []

    def setSubtitleTrack(self, sid):
        """sid=None/0 turns captions off."""
        if self._closed:
            return
        try:
            self._mpv.sid = int(sid) if sid else "no"
        except Exception as exc:
            self.errorOccurred.emit(str(exc))

    def position(self):
        return self._position_ms

    def duration(self):
        return self._duration_ms

    def playbackState(self):
        return self._state

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._mpv.terminate()
        except Exception:
            pass
