"""One cache file per week for the schedule screen.

Holds the global schedule and, per provider, which shows were confirmed on it.
The first launch after Monday 00:00 starts a fresh file; everything is kept
until then so the app never has to rebuild the whole week again.
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta

from hayate.core.settings import cache_dir

GLOBAL_TTL = 6 * 3600        # re-fetch the global schedule if older (delays/changes)
RECHECK_AFTER = 3 * 3600     # re-check a missing show at most this often
RECENT_WINDOW = 24 * 3600    # ...and only while an episode aired within this window

_lock = threading.RLock()
_state = None


def _week_start() -> int:
    now = datetime.now()
    monday = datetime.combine(now.date() - timedelta(days=now.weekday()), datetime.min.time())
    return int(monday.timestamp())


def _path():
    return cache_dir() / "week_cache.json"


def _save_unlocked():
    try:
        tmp = _path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_state), encoding="utf-8")
        os.replace(tmp, _path())
    except Exception:
        pass


def _load():
    global _state
    if _state is None:
        try:
            _state = json.loads(_path().read_text(encoding="utf-8"))
        except Exception:
            _state = {}
    ws = _week_start()
    if _state.get("week_start") != ws:  # new week: drop last week's cache
        _state = {"week_start": ws, "global": None, "in_app": {}}
        _save_unlocked()
    return _state


def save():
    with _lock:
        _load()
        _save_unlocked()


# ---- global schedule ----
def get_global(allow_adult: bool):
    with _lock:
        g = _load().get("global")
        if g and g.get("allow_adult") == allow_adult and time.time() - g.get("fetched_at", 0) < GLOBAL_TTL:
            return g["data"]
    return None


def set_global(data: dict, allow_adult: bool):
    with _lock:
        _load()["global"] = {"data": data, "allow_adult": allow_adult, "fetched_at": time.time()}
        _save_unlocked()


# ---- in-app (provider-confirmed) releases ----
def _prov(provider: str) -> dict:
    return _load().setdefault("in_app", {}).setdefault(provider, {"ok": {}, "checked": {}})


def ok_ids(provider: str) -> set:
    with _lock:
        return {int(k) for k in _prov(provider)["ok"]}


def record(provider: str, anime_id: int, found: bool):
    with _lock:
        p = _prov(provider)
        p["checked"][str(anime_id)] = time.time()
        if found:
            p["ok"][str(anime_id)] = time.time()  # once confirmed, it stays for the week


def needs_check(provider: str, anime_id: int, airings: list) -> bool:
    """Only new shows, or missing ones that have had a new episode air since the last check."""
    with _lock:
        p = _prov(provider)
        k = str(anime_id)
        if k in p["ok"]:
            return False
        last = p["checked"].get(k)
    if last is None:
        return True
    now = time.time()
    aired = [t for t in airings if t and t <= now]
    if any(t > last for t in aired):
        return True
    return bool(aired) and now - last > RECHECK_AFTER and max(aired) > now - RECENT_WINDOW


# ---- per-episode confirmation (only episodes that really play get listed) ----
RECHECK_EP = 2 * 3600          # re-check a missing aired episode this often...
EP_WINDOW = 3 * 24 * 3600      # ...for this long after it aired (uploads can lag)


def _eps(provider: str) -> dict:
    return _prov(provider).setdefault("eps", {})


def record_ep(provider: str, anime_id: int, ep: int, found: bool, langs=None):
    with _lock:
        _eps(provider)[f"{anime_id}:{ep}"] = [time.time(), bool(found), list(langs or [])]


def ep_langs(provider: str, anime_id: int, ep: int) -> list:
    with _lock:
        v = _eps(provider).get(f"{anime_id}:{ep}")
        return list(v[2]) if v and len(v) > 2 else []


def show_langs(provider: str, anime_id: int) -> list:
    with _lock:
        pre, out = f"{anime_id}:", set()
        for k, v in _eps(provider).items():
            if k.startswith(pre) and v[1] and len(v) > 2:
                out.update(v[2])
        return sorted(out)


def ep_ok(provider: str, anime_id: int, ep: int) -> bool:
    with _lock:
        v = _eps(provider).get(f"{anime_id}:{ep}")
        return bool(v and v[1])


def show_ok(provider: str, anime_id: int) -> bool:
    with _lock:
        pre = f"{anime_id}:"
        return any(k.startswith(pre) and v[1] for k, v in _eps(provider).items())


def ep_needs_check(provider: str, anime_id: int, ep: int, airing_at: int) -> bool:
    with _lock:
        v = _eps(provider).get(f"{anime_id}:{ep}")
    if v is None:
        return True
    if v[1]:
        return False
    now = time.time()
    return bool(airing_at) and airing_at > now - EP_WINDOW and now - v[0] > RECHECK_EP
