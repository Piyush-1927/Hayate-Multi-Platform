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
    "muse india - best anime collection",
    "muse philippines",
    "muse vi\u1ec7t nam",
    "ani-one philippines",
    "ani-one indonesia",
    "ani-one india",
    "crunchyroll dubs",
    "gundam channel intl",
    "tms entertainment",
    "nozomi entertainment",
    "retrocrush",
    "toei animation",
    "\u6771\u6620\u52d5\u756b\u4e9e\u6d32 toei animation asia",
    "aniplus asia",
}


_NOWIN = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _ylog(*a):
    """Append to <config>/ytdlp.log (windowed builds have no console)."""
    try:
        import time
        from hayate.core.settings import config_dir
        with open(config_dir() / "ytdlp.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a) + "\n")
    except Exception:
        pass


class YtDlpProvider(StreamProvider):
    name = "ytdlp"
    label = "Official Licensors (YouTube)"
    supports_catalog = True

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

    _MEMBERS = re.compile(r"\bultra\b|ultra\s*early|members?[- ]only|會員|会员", re.I)

    def _usable(self, data, title, episode):
        """Official channel + this exact episode + free (no ULTRA / members-only)."""
        v = data.get("title", "") or ""
        if not (self._is_official_channel(data.get("uploader", ""), data.get("channel", ""))
                and self._matches_episode(v, episode)):
            return False
        return not (self._MEMBERS.search(v)
                    or data.get("availability") in ("subscriber_only", "premium_only", "needs_auth"))

    _HIN = re.compile(r"hindi|हिन्दी|हिंदी", re.I)
    _HIN_SUB = re.compile(r"hindi\s*sub", re.I)
    _ENG_DUB = re.compile(r"english\s*dub|eng\s*dub|dubbed\s*in\s*english|english\s*dubbed", re.I)
    _ANY_DUB = re.compile(r"\bdub(bed|s)?\b|配音|吹き替え|吹替|dublado|doblaje|lồng tiếng|พากย์", re.I)

    def _lang(self, data, custom=None):
        """sub / eng / hin / dub (a dub in some other language)."""
        t = data.get("title") or ""
        ch = (data.get("channel") or data.get("uploader") or "").lower()
        if custom and custom in (t + " " + ch).lower() and (self._ANY_DUB.search(t) or "dub" in ch):
            return "c:" + custom
        if (self._HIN.search(t) and not self._HIN_SUB.search(t)) or "hindi dub" in ch:
            return "hin"
        if self._ENG_DUB.search(t) or "dubs" in ch:
            return "eng"
        if self._ANY_DUB.search(t):
            return "dub"
        return "sub"

    @staticmethod
    def _want(dub):
        if dub in (None, False, "", "sub"):
            return "sub"
        if dub is True:
            return "any_dub"
        if isinstance(dub, str) and dub.startswith("custom:") and dub[7:].strip():
            return "c:" + dub[7:].strip().lower()
        return {"dub_eng": "eng", "eng": "eng", "dub_hin": "hin", "hin": "hin",
                "any_dub": "any_dub", "any": "any"}.get(str(dub), "sub")

    @staticmethod
    def _lang_ok(lang, want):
        return want == "any" or (want == "any_dub" and lang != "sub") or lang == want

    def episode_langs(self, title, episode):
        """Languages (sub/eng/hin/dub) of free official uploads of this episode; None = couldn't tell."""
        exe = self._find_ytdlp()
        if not exe:
            return None
        base = f"{self._clean_title(title)} episode {episode}"
        langs, ok = set(), False
        for extra in ("", " hindi dub", " english dub"):
            try:
                r = subprocess.run([exe, *net.ytdlp_args(), "--flat-playlist", "--no-warnings", "-j",
                                    f"ytsearch10:{base}{extra}"],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, **_NOWIN)
            except Exception:
                continue
            if r.returncode != 0 and not r.stdout.strip():
                continue
            ok = True
            for line in r.stdout.splitlines():
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if self._usable(d, title, episode):
                    langs.add(self._lang(d))
        return sorted(langs) if ok else None

    def has_episode(self, title, episode):
        l = self.episode_langs(title, episode)
        return None if l is None else bool(l)

    def get_stream(self, title: str, episode: int, dub=False, quality="best"):
        ytdlp_bin = self._find_ytdlp()
        _ylog("exe", ytdlp_bin)
        if not ytdlp_bin:
            return None

        clean = self._clean_title(title)
        want = self._want(dub)
        custom = want[2:] if want.startswith("c:") else None
        query = f"ytsearch10:{clean} episode {episode}" + {"hin": " hindi dub", "eng": " english dub", "any_dub": " dub"}.get(want, f" {custom} dub" if custom else "")

        # Step 1: Ultra-fast metadata scan (<1 sec) using --flat-playlist
        scan_cmd = [
            ytdlp_bin,
            "--flat-playlist",
            "--no-warnings",
            "-j",
            query,
        ]

        self.last_notice = ""
        found_langs = set()
        target_url = None
        matched_title = ""
        matched_uploader = ""

        try:
            res = subprocess.run(scan_cmd[:1] + net.ytdlp_args() + scan_cmd[1:], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, **_NOWIN)
            _ylog("scan rc", res.returncode, "out", len(res.stdout or ""), "err", (res.stderr or "")[-200:], "q", query)
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.strip().splitlines():
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue

                    uploader = data.get("uploader", "")
                    channel = data.get("channel", "")
                    v_title = data.get("title", "")

                    if self._usable(data, title, episode):
                        found_langs.add(self._lang(data, custom))
                    if self._usable(data, title, episode) and self._lang_ok(self._lang(data, custom), want):
                        target_url = data.get("url") or f"https://www.youtube.com/watch?v={data.get('id')}"
                        matched_title = v_title
                        matched_uploader = uploader or channel
                        break

            if not target_url and not found_langs and want != "sub":
                try:
                    r2 = subprocess.run([ytdlp_bin, *net.ytdlp_args(), "--flat-playlist", "--no-warnings", "-j",
                                         f"ytsearch10:{clean} episode {episode}"],
                                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                                        timeout=30, **_NOWIN)
                    _ylog("fallback scan rc", r2.returncode, "out", len(r2.stdout or ""))
                    for ln in (r2.stdout or "").splitlines():
                        try:
                            d = json.loads(ln)
                        except Exception:
                            continue
                        if self._usable(d, title, episode):
                            found_langs.add(self._lang(d, custom))
                except Exception as e:
                    _ylog("fallback scan failed", repr(e))

            # If no official channel uploaded this episode, return None cleanly
            if not target_url:
                if found_langs:
                    nm = {"sub": "Sub", "eng": "English dub", "hin": "Hindi dub", "dub": "Another dub"}
                    wn = {"sub": "Sub", "eng": "English dub", "hin": "Hindi dub",
                          "any_dub": "A dub", "any": "This episode"}.get(want, f"{custom.title()} dub" if custom else "That audio")
                    order = ["sub", "eng", "hin", "dub"]
                    have = ", ".join([nm[k] for k in order if k in found_langs]
                                     + [k[2:].title() + " dub" for k in sorted(found_langs) if k.startswith("c:")])
                    verb = "isn't" if want != "any_dub" else "isn't"
                    self.last_notice = (f"{wn} {verb} available for episode {episode} on this provider. "
                                        f"Available: {have}.")
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
            ext_res = subprocess.run(extract_cmd[:1] + net.ytdlp_args() + extract_cmd[1:], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40, **_NOWIN)
            _ylog("extract rc", ext_res.returncode, "err", (ext_res.stderr or "")[-200:])
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
            _ylog("ERROR", repr(e))
            self.last_notice = f"yt-dlp failed: {e}"

        return None

    DEFAULT_CHANNELS = ("https://www.youtube.com/@MuseAsia", "https://www.youtube.com/@AniOneAsia")
    _JUNK = re.compile(r"highlight|recap|marathon|celebration|anniversary|ani-?mall|\bfest\b|\bPV\b|theme song|ani-one studio|預告|精華|best scene|in theaters|ani-news|ani-stars|digital live|pop-up|anime music|reveal|interview|messages from|promotion|after hours|新番|購物網|現場直擊|訂閱|quizknock|mecha-senku|science class", re.I)  # promo reels, not series
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
        idx = {}
        for url in self._channel_urls():
            try:
                r = subprocess.run([exe, *net.ytdlp_args(), "--flat-playlist", "-J", "--no-warnings", url.rstrip("/") + "/playlists"],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, **_NOWIN)
                _d = json.loads(r.stdout)
                entries = _d.get("entries") or []
                _chan = (_d.get("channel") or _d.get("uploader") or _d.get("title") or "")
            except Exception as e:
                print(f"[catalogue] {url}: {e}")
                failed = True
                continue
            for e in entries:
                if self._JUNK.search(e.get("title") or ""): continue
                t = re.sub(r"\(.*?\)|\[.*?\]", "", e.get("title") or "")
                t = re.sub(r"\s+", " ", self._NOISE.sub("", t)).strip(" |·-")
                _k = self._norm(t)
                if _k and not self._MEMBERS.search(e.get("title") or ""):
                    idx.setdefault(_k, set()).add(self._lang({"title": e.get("title") or "", "channel": _chan}))
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
            try:
                self._langs_file().write_text(json.dumps(
                    {"t": time.time(), "idx": {k: sorted(v) for k, v in idx.items()}}))
            except Exception:
                pass
        return out

    _cat_lock = __import__("threading").Lock()
    _cat_norm = None

    @staticmethod
    def _norm(t):
        t = (t or "").lower()
        t = re.sub(r"\b(season|part|cour)\s*\d+\b|\b\d+(st|nd|rd|th)\s+(season|part|cour)\b|\bfinal season\b", " ", t)
        t = re.sub(r"[^a-z0-9]+", " ", t)
        return re.sub(r"\s+", " ", t).strip()

    _lang_idx = None
    _warm_started = False

    def __init__(self):
        super().__init__()
        if not YtDlpProvider._warm_started:  # one background scan per app start
            YtDlpProvider._warm_started = True
            import threading
            threading.Thread(target=self._warm_langs, daemon=True).start()

    def _langs_file(self):
        from hayate.core.settings import config_dir
        return config_dir() / "catalogue_langs.json"

    _PUB = re.compile(r"\b(muse|ani one)((?: (?:in|india|asia|indonesia|malaysia|thailand|philippines|vietnam|vi t nam|id|hk|ultra))*)$")

    def _lkey(self, t):
        """Lookup key for the language index: drops publisher suffixes and stray 'x' tokens."""
        n = self._norm(t)
        n = self._PUB.sub("", n).strip()
        return re.sub(r"\s+", " ", re.sub(r"\bx\b", " ", n)).strip()

    def _load_lang_idx(self):
        import time
        try:
            d = json.loads(self._langs_file().read_text())
            if time.time() - d["t"] < 86400:
                idx = {}
                for k, v in d["idx"].items():
                    idx.setdefault(self._lkey(k), set()).update(v)
                idx.pop("", None)
                self._lang_idx = idx
                return True
        except Exception:
            pass
        return False

    def _warm_langs(self):
        """Startup: make sure the language index exists (scans the channels if it's missing or stale)."""
        import time; time.sleep(30)
        try:
            with self._cat_lock:
                if self._load_lang_idx():
                    return
                self.catalogue_titles(refresh=True)  # also writes catalogue_langs.json
                self._load_lang_idx()
        except Exception as e:
            print(f"[langs] startup scan failed: {e}")

    def cached_langs(self, title):
        """Languages from the startup scan: a list (empty = not on YouTube), or None if not ready yet."""
        if self._lang_idx is None and not self._load_lang_idx():
            return None
        idx, q = self._lang_idx, self._lkey(title)
        if not q:
            return []
        if q in idx:
            return sorted(idx[q])
        found, pq = set(), f" {q} "
        for k, v in idx.items():
            if len(k) >= 4 and (f" {k} " in pq or pq in f" {k} "):
                found |= v
        return sorted(found)

    def carries(self, title: str):
        """True/False if the official channels carry this show; None = couldn't tell (retry later)."""
        import difflib
        with self._cat_lock:  # other worker threads wait while the catalogue loads once
            if not self._cat_norm:
                n = {self._norm(t) for t in self.catalogue_titles() if not self._MEMBERS.search(t)}
                n.discard("")
                if not n:
                    return None
                self._cat_norm = n
            cat = self._cat_norm
        q = self._norm(title)
        if not q:
            return False
        if q in cat:
            return True
        pq = f" {q} "
        for c in cat:
            if len(c) >= 4 and (f" {c} " in pq or pq in f" {c} "):
                return True
        sm = difflib.SequenceMatcher(autojunk=False)
        sm.set_seq2(q)
        for c in cat:
            sm.set_seq1(c)
            if sm.real_quick_ratio() >= 0.88 and sm.quick_ratio() >= 0.88 and sm.ratio() >= 0.88:
                return True
        return False
