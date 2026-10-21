import shutil
import subprocess
import sys
import threading
from pathlib import Path

from hayate.core.settings import DEFAULTS, config_dir, load_settings, save_settings
from hayate.providers import Stream, load_providers, provider_label

NO_STREAM_MSG = (
    "No stream source found for this episode.\n\n"
    "The default source only plays official uploads from the channels listed in "
    "~/.config/hayate/settings.json, so some titles won't be available. "
    "You can also add your own source in ~/.config/hayate/providers/."
)


_NOWIN = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def ytdl_format(quality: str) -> str:
    q = (quality or "best").lower().rstrip("p")
    if q.isdigit():
        return f"bestvideo[height<={q}]+bestaudio/best[height<={q}]/best"
    return "bestvideo+bestaudio/best"


def find_ytdlp():
    exe = Path(sys.executable).parent / ("yt-dlp.exe" if sys.platform == "win32" else "yt-dlp")
    return str(exe) if exe.exists() else shutil.which("yt-dlp")


class PlayerController:
    def __init__(self):
        if not (config_dir() / "settings.json").exists():
            save_settings(dict(DEFAULTS))
        self.providers = load_providers()
        self.proc = None

    def stop(self):
        """Terminates any active mpv process."""
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=1.5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    def active_provider(self):
        name = load_settings().get("provider", "ytdlp")
        return self.providers.get(name) or (next(iter(self.providers.values())) if self.providers else None)

    def active_provider_label(self) -> str:
        return provider_label(self.active_provider())

    def has_alternate_providers(self) -> bool:
        return len(self.providers) > 1

    def reload_providers(self):
        """Re-scans ~/.config/hayate/providers/ and updates in memory."""
        self.providers = load_providers()
        return self.providers

    def play(self, title, episode, dub=False, quality="best", wid=None, on_player_started=None, on_finished=None):
        def finish(kind, msg):
            # kind: "no_stream" (provider has nothing) or "error" (something broke)
            if on_finished:
                on_finished(kind, msg)

        def run():
            provider = self.active_provider()
            is_default = getattr(provider, "name", "") == "ytdlp"

            # Dynamic timeout allowance based on active provider
            timeout_budget = 5 if is_default else 15

            try:
                stream = provider.get_stream(title, episode, dub=dub, quality=quality)
            except Exception as e:
                finish("error", f"The stream source failed: {e}")
                return
            if not stream:
                finish("no_stream", NO_STREAM_MSG)
                return
            if not isinstance(stream, Stream) or not isinstance(stream.url, str) or not stream.url:
                finish("error", "The stream source returned an invalid stream.")
                return

            self.stop()

            mpv = shutil.which("mpv")
            if not mpv:
                finish("error", "mpv is not installed. Install it (sudo pacman -S mpv) and try again.")
                return

            cmd = [
                mpv,
                f"--force-media-title={stream.title or title} - EP {episode}",
                f"--ytdl-format={ytdl_format(quality)}",
            ]
            if wid:
                cmd.extend([f"--wid={wid}", "--input-default-bindings=yes", "--osc=yes"])
            ytdlp = find_ytdlp()
            if ytdlp:
                cmd.append(f"--script-opts=ytdl_hook-ytdl_path={ytdlp}")
            if stream.referer:
                cmd.append(f"--referrer={stream.referer}")
            if stream.headers:
                cmd.append("--http-header-fields=" + ",".join(f"{k}: {v}" for k, v in stream.headers.items()))
            for sub in stream.subtitles:
                cmd.append(f"--sub-file={sub}")
            try:
                from hayate.core import net as _net
                _u = _net.route_url()
            except OSError as exc:  # proxy enabled but unusable: never play direct
                finish("error", f"Network proxy error: {exc}")
                return
            if _u:
                cmd.extend([f"--http-proxy={_u}", f"--ytdl-raw-options=proxy={_u}"])
            cmd.append(stream.url)

            try:
                self.proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                    **_NOWIN,
                )
            except Exception as e:
                finish("error", f"Could not launch mpv: {e}")
                return

            started = False
            # Read stdout chunk by chunk to handle \r progress lines
            while self.proc.poll() is None:
                line = self.proc.stdout.readline()
                if not line:
                    break
                low = line.lower()
                if not started and ("vo:" in low or "ao:" in low or "av:" in low or "playback restarted" in low or "cplayer" in low or "cache:" in low or "playing" in low or "v:" in low):
                    started = True
                    if on_player_started:
                        on_player_started()

            self.proc.stdout.close()
            ret = self.proc.wait()

            if not started and ret != 0:
                finish("error", "mpv exited before playback started.")

        threading.Thread(target=run, daemon=True).start()
