"""AniSkip (api.aniskip.com) OP/ED timestamps, fetched off-thread through the app's net bridge."""
import threading

from PyQt6.QtCore import QObject, pyqtSignal

from hayate.core import net

_API = "https://api.aniskip.com/v2/skip-times/{mal}/{ep}"
_ANILIST = "https://graphql.anilist.co"
_mal_cache: dict[int, int] = {}
_cache: dict[tuple[int, int], dict] = {}


def _fetch(anilist_id: int, ep: int) -> dict:
    """Returns {"op": (start_ms, end_ms), "ed": (start_ms, end_ms)}; missing keys = not found."""
    with net.client(timeout=12) as c:
        mal = _mal_cache.get(anilist_id)
        if mal is None:
            r = c.post(_ANILIST, json={
                "query": "query($id:Int){Media(id:$id){idMal}}",
                "variables": {"id": anilist_id}})
            mal = (((r.json().get("data") or {}).get("Media") or {}).get("idMal")) or 0
            _mal_cache[anilist_id] = mal
        if not mal:
            return {}
        r = c.get(_API.format(mal=mal, ep=ep),
                  params=[("types", "op"), ("types", "ed"), ("episodeLength", 0)])
        if r.status_code != 200:
            return {}
        out = {}
        for it in r.json().get("results", []):
            iv = it.get("interval") or {}
            t = it.get("skipType")
            if t in ("op", "ed") and "startTime" in iv and "endTime" in iv:
                out[t] = (int(iv["startTime"] * 1000), int(iv["endTime"] * 1000))
        return out


class AniSkip(QObject):
    ready = pyqtSignal(int, int, dict)  # anilist_id, episode, segments

    def request(self, anilist_id: int, ep: int):
        key = (anilist_id, ep)
        if key in _cache:
            self.ready.emit(anilist_id, ep, _cache[key])
            return

        def run():
            try:
                seg = _fetch(anilist_id, ep)
            except Exception:
                seg = {}
            _cache[key] = seg
            self.ready.emit(anilist_id, ep, seg)

        threading.Thread(target=run, daemon=True).start()
