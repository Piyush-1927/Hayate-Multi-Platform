from MediaPlayer import (MPNowPlayingInfoCenter, MPRemoteCommandCenter, MPMediaItemPropertyTitle,
                         MPMediaItemPropertyPlaybackDuration, MPNowPlayingInfoPropertyElapsedPlaybackTime,
                         MPNowPlayingInfoPropertyPlaybackRate, MPNowPlayingPlaybackStatePlaying,
                         MPNowPlayingPlaybackStatePaused, MPNowPlayingPlaybackStateStopped)
from hayate.ui.media_controls import _Base

_ST = {"Playing": MPNowPlayingPlaybackStatePlaying, "Paused": MPNowPlayingPlaybackStatePaused,
       "Stopped": MPNowPlayingPlaybackStateStopped}


class MacMedia(_Base):
    def __init__(self, callbacks, position_fn=lambda: 0, parent=None):
        super().__init__(callbacks, position_fn, parent)
        self._ctr = MPNowPlayingInfoCenter.defaultCenter()
        rc = self._rc = MPRemoteCommandCenter.sharedCommandCenter()
        self._h = []  # keep handler refs alive
        for cmd, name in ((rc.playCommand(), "play"), (rc.pauseCommand(), "pause"),
                          (rc.togglePlayPauseCommand(), "toggle"), (rc.stopCommand(), "stop"),
                          (rc.nextTrackCommand(), "next"), (rc.previousTrackCommand(), "prev")):
            cmd.setEnabled_(name not in ("next", "prev"))
            self._add(cmd, lambda e, n=name: (self._cmd.emit(n, None), 0)[1])
        rc.changePlaybackPositionCommand().setEnabled_(True)
        self._add(rc.changePlaybackPositionCommand(),
                  lambda e: (self._cmd.emit("seek_abs", int(e.positionTime() * 1e6)), 0)[1])
        self.ok = True

    def _add(self, cmd, fn):
        self._h.append(fn)
        cmd.addTargetWithHandler_(fn)

    def _push(self):
        try:
            self._rc.nextTrackCommand().setEnabled_(self.can_next)
            self._rc.previousTrackCommand().setEnabled_(self.can_prev)
            self._ctr.setNowPlayingInfo_({
                MPMediaItemPropertyTitle: self.title or "Hayate",
                MPMediaItemPropertyPlaybackDuration: self.length_us / 1e6,
                MPNowPlayingInfoPropertyElapsedPlaybackTime: self._pos() / 1e6,
                MPNowPlayingInfoPropertyPlaybackRate: 1.0 if self.status == "Playing" else 0.0,
            })
            self._ctr.setPlaybackState_(_ST.get(self.status, MPNowPlayingPlaybackStateStopped))
        except Exception as e:
            print("NowPlaying update failed:", e, flush=True)
