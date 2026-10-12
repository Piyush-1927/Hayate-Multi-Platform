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
