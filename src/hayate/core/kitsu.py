"""Kitsu browse for the Global catalogue. Uses core.net so proxy/Tor/DNS settings apply."""
import pickle, threading, time
from hayate.core import net
from hayate.core.anilist import AnimeMedia

BASE = "https://kitsu.io/api/edge/anime"
LIMIT = 20          # Kitsu's max page size; chunk c covers raw positions [c*20, c*20+20)
TTL = 24 * 3600
_lock, _cache = threading.Lock(), None


def _file():
    from hayate.core.settings import config_dir
    return config_dir() / "catalogue_kitsu.pkl"


def _load():
    global _cache
    if _cache is None:
        try: _cache = pickle.loads(_file().read_bytes())
        except Exception: _cache = {}
    return _cache


def save():
    with _lock:
        try:
            f = _file(); f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(pickle.dumps(_load()))
        except Exception: pass


_qc = {}


def get_chunk(c, text="", cats=(), extra=()):
    """(list aligned to raw positions, None where skipped; total). Default browse: cached 24h on disk,
    stale cache if offline. Search/filter queries: memory-only, 10 min."""
    if text or cats or extra:
        key = (c, text.lower(), tuple(cats), tuple(extra))
        with _lock: hit = _qc.get(key)
        if hit and time.time() - hit[0] < 600: return hit[1], hit[2]
        items, total = _fetch(c * LIMIT, text, tuple(cats), tuple(extra))
        with _lock:
            if len(_qc) > 400: _qc.clear()
            _qc[key] = (time.time(), items, total)
        return items, total
    with _lock: hit = _load().get(c)
    if hit and time.time() - hit[0] < TTL: return hit[1], hit[2]
    try: items, total = _fetch(c * LIMIT)
    except Exception:
        if hit: return hit[1], hit[2]
        raise
    with _lock: _load()[c] = (time.time(), items, total)
    return items, total


def _fetch(offset, text="", cats=(), extra=()):
    params = {
        "page[limit]": LIMIT, "page[offset]": offset, "sort": "-userCount", "include": "mappings",
        "fields[anime]": "canonicalTitle,titles,posterImage,mappings,ageRating,nsfw,synopsis,"
                         "episodeCount,startDate,subtype,averageRating",
        "fields[mappings]": "externalSite,externalId",
    }
    if text: params["filter[text]"] = text; params.pop("sort", None)  # text search: relevance order
    if cats: params["filter[categories]"] = ",".join(cats)  # comma = AND
    for k, v in extra: params[k] = v  # sort / season / status
    with net.client(timeout=12) as c:
        r = c.get(BASE, params=params, headers={"Accept": "application/vnd.api+json"})
        r.raise_for_status(); d = r.json()
    al = {m["id"]: m["attributes"]["externalId"] for m in d.get("included", [])
          if (m.get("attributes") or {}).get("externalSite") == "anilist/anime"}
    out = []
    for a in d.get("data", []):
        at = a.get("attributes") or {}
        refs = ((a.get("relationships") or {}).get("mappings") or {}).get("data") or []
        aid = next((al[x["id"]] for x in refs if x["id"] in al), None)
        poster = (at.get("posterImage") or {}).get("small")
        if not aid or not poster or at.get("nsfw") or at.get("ageRating") == "R18":
            out.append(None); continue
        t = at.get("titles") or {}
        try: score = int(round(float(at.get("averageRating"))))
        except (TypeError, ValueError): score = None
        sd = at.get("startDate") or ""
        out.append(AnimeMedia(
            id=int(aid), title=t.get("en") or at.get("canonicalTitle") or t.get("en_jp") or "Unknown Title",
            cover_url=poster, description=at.get("synopsis") or "",
            format=(at.get("subtype") or "").upper() or None, episodes=at.get("episodeCount") or 1,
            average_score=score, season_year=int(sd[:4]) if sd[:4].isdigit() else None))
    return out, int((d.get("meta") or {}).get("count") or 0)
