import os
import subprocess
from hayate.core import net
import json
import shutil
import re
import sys
from pathlib import Path

from hayate.providers.base import StreamProvider, Stream


OFFICIAL_CHANNELS = {
    # Muse Communication
    "muse asia",
    "muse india",
    "muse malaysia",
    "muse indonesia",
    "muse vietnam",
    "muse thailand",
    # Medialink Ani-One
    "ani-one asia",
    "ani-one thailand",
    "ani-one vietnam",
    "ani-one china",
    # Official Distributors & Publishers
    "crunchyroll",
    "crunchyroll collection",
    "tms anime",
    "gundaminfo",
    "gundam.info",
    "pokemon asia official",
    "bilibili",
    "kadokawa anime",
    "toho animation",
    "aniplex",
    "bandai spirits",
}


_NOWIN = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class YtDlpProvider(StreamProvider):
    name = "ytdlp"
    label = "Official Licensors (YouTube)"
    supports_catalog = False

    def _find_ytdlp(self):
        # Prefer the normal PATH lookup so existing system installs keep working.
        ytdlp = shutil.which("yt-dlp")
        if ytdlp:
            return ytdlp

        # Also check the active Python environment. On Windows, pip installs
        # command-line scripts under Scripts; on POSIX systems, under bin.
        executable_name = "yt-dlp.exe" if os.name == "nt" else "yt-dlp"
        script_dir = "Scripts" if os.name == "nt" else "bin"
        from hayate.core.settings import config_dir
        bundle = Path(getattr(sys, "_MEIPASS", sys.prefix))
        candidates = (
            Path(sys.executable).resolve().parent / executable_name,
            Path(sys.prefix) / script_dir / executable_name,
            # frozen builds: binary bundled at the bundle root
            bundle / executable_name,
            bundle / script_dir / executable_name,
            # user-installed copy, works on any machine without PATH
            config_dir() / "bin" / executable_name,
        )

        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)

        return None

    def _clean_title(self, raw_title: str) -> str:
        t = re.sub(r'\(.*?\)|\[.*?\]', '', raw_title)
        t = re.sub(r'[:\-–—]', ' ', t)
        t = re.sub(r'\s+', ' ', t).strip()
        return t

    def _is_official_channel(self, uploader: str, channel: str) -> bool:
        up = (uploader or "").strip().lower()
        ch = (channel or "").strip().lower()
        for official in OFFICIAL_CHANNELS:
            if official in up or official in ch:
                return True
        return False

    def _matches_episode(self, video_title: str, episode: int) -> bool:
        title_lower = video_title.lower()
        n = rf"0*{episode}"  # titles zero-pad: "Episode 01"
        patterns = [
            rf"\bep(isode)?\.?\s*{n}\b",
            rf"#{n}\b",
            rf"\[{n}\]",
            rf"\({n}\)",
        ]
        return any(re.search(pat, title_lower) for pat in patterns)

    def get_stream(self, title: str, episode: int, dub=False, quality="best"):
        ytdlp_bin = self._find_ytdlp()
        if not ytdlp_bin:
            return None

        clean = self._clean_title(title)
        query = f"ytsearch10:{clean} episode {episode}"

        # Step 1: Ultra-fast metadata scan (<1 sec) using --flat-playlist
        scan_cmd = [
            ytdlp_bin,
            "--flat-playlist",
            "--no-warnings",
            "-j",
            query,
        ]

        target_url = None
        matched_title = ""
        matched_uploader = ""

        try:
            res = subprocess.run(scan_cmd[:1] + net.ytdlp_args() + scan_cmd[1:], capture_output=True, text=True, timeout=8, **_NOWIN)
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.strip().splitlines():
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue

                    uploader = data.get("uploader", "")
                    channel = data.get("channel", "")
                    v_title = data.get("title", "")

                    if self._is_official_channel(uploader, channel) and self._matches_episode(v_title, episode):
                        target_url = data.get("url") or f"https://www.youtube.com/watch?v={data.get('id')}"
                        matched_title = v_title
                        matched_uploader = uploader or channel
                        break

            # If no official channel uploaded this episode, return None cleanly
            if not target_url:
                return None

            # Step 2: Resolve stream URL only for the verified video
            extract_cmd = [
                ytdlp_bin,
                "--no-playlist",
                "--extractor-args", "youtube:player_client=android",
                "-f", "best[ext=mp4]/best",
                "-j",
                target_url,
            ]
            ext_res = subprocess.run(extract_cmd[:1] + net.ytdlp_args() + extract_cmd[1:], capture_output=True, text=True, timeout=12, **_NOWIN)
            if ext_res.returncode == 0 and ext_res.stdout.strip():
                meta = json.loads(ext_res.stdout.strip().splitlines()[0])
                direct_url = meta.get("url")
                if direct_url:
                    return Stream(
                        url=direct_url,
                        title=f"[{matched_uploader}] {matched_title}",
                        headers=meta.get("http_headers", {}),
                    )

        except Exception as e:
            print(f"[yt-dlp official provider error]: {e}")

        return None

    DEFAULT_CHANNELS = ("https://www.youtube.com/@MuseAsia", "https://www.youtube.com/@AniOneAsia")
    _NOISE = re.compile(r"multi[- ]?subs?|full episodes?|playlist|english sub(bed)?|eng sub|\bsubbed\b|\bdubbed\b", re.I)

    def _channel_urls(self):
        from hayate.core.settings import config_dir
        f = config_dir() / "catalogue_channels.txt"  # one channel URL per line, # = comment
        extra = []
        if f.is_file():
            extra = [l.strip() for l in f.read_text().splitlines() if l.strip() and not l.startswith("#")]
        return [*self.DEFAULT_CHANNELS, *extra]

    def catalogue_titles(self, refresh=False):
        """Series titles from official channels' playlists (cached 24h)."""
        import time
        from hayate.core.settings import config_dir
        cache = config_dir() / "catalogue_cache.json"
        stale = []
        if cache.is_file():
            try:
                d = json.loads(cache.read_text())
                stale = d.get("titles") or []
                if not refresh and time.time() - d["t"] < 86400:
                    return stale
            except Exception:
                pass
        exe = self._find_ytdlp()
        if not exe:
            return stale
        failed = False
        seen, out = set(), []
        for url in self._channel_urls():
            try:
                r = subprocess.run([exe, *net.ytdlp_args(), "--flat-playlist", "-J", "--no-warnings", url.rstrip("/") + "/playlists"],
                                   capture_output=True, text=True, timeout=90, **_NOWIN)
                entries = json.loads(r.stdout).get("entries") or []
            except Exception as e:
                print(f"[catalogue] {url}: {e}")
                failed = True
                continue
            for e in entries:
                t = re.sub(r"\(.*?\)|\[.*?\]", "", e.get("title") or "")
                t = re.sub(r"\s+", " ", self._NOISE.sub("", t)).strip(" |·-")
                if len(t) > 2 and t.lower() not in seen:
                    seen.add(t.lower()); out.append(t)
        if failed and stale:
            # offline / a channel failed: keep known titles, don't overwrite the
            # good cache with a shrunken list, and retry on the next open
            for t in stale:
                if t.lower() not in seen:
                    seen.add(t.lower()); out.append(t)
            return out
        if out:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"t": time.time(), "titles": out}))
        return out

    def carries(self, title: str):
        return True
