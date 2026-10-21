import urllib.request
from hayate.core import net
import urllib.error
import json
import time
from datetime import datetime, timedelta, date
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict, Any

from hayate.core.settings import config_dir, load_settings, cache_dir

ANILIST_API_URL = "https://graphql.anilist.co"

DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

SCHEDULE_CACHE_TTL = 30 * 60  # seconds
SCHEDULE_FORMATS = {"TV", "TV_SHORT", "ONA"}


@dataclass
class AnimeMedia:
    id: int
    title: str
    cover_url: str = ""
    banner_url: str = ""
    description: str = ""
    format: Optional[str] = None
    episodes: int = 1
    average_score: Optional[int] = None
    season: Optional[str] = None
    season_year: Optional[int] = None
    genres: List[str] = field(default_factory=list)
    is_adult: bool = False
    relations: List[Dict[str, Any]] = field(default_factory=list)
    airing_episode: Optional[int] = None
    airing_time: Optional[int] = None
    start_date: Optional[Tuple[Optional[int], Optional[int], Optional[int]]] = None
    end_date: Optional[Tuple[Optional[int], Optional[int], Optional[int]]] = None
    status: Optional[str] = None

    @property
    def display_title(self) -> str:
        return self.title or "Unknown Title"

    @property
    def year(self) -> Optional[int]:
        return self.season_year

    @property
    def has_prequel(self) -> bool:
        return any(r.get("relation_type") == "PREQUEL" for r in self.relations)

    @property
    def is_date_confirmed(self) -> bool:
        """True when AniList has at least a year and month for the premiere."""
        return bool(self.start_date and self.start_date[0] and self.start_date[1])


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------

def _send_query(query: str, variables: Optional[dict] = None, _retried: bool = False) -> dict:
    payload = {"query": query, "variables": variables or {}}
    data_bytes = json.dumps(payload).encode("utf-8")
    with net.client(timeout=12) as _c:
        resp = _c.post(
            ANILIST_API_URL,
            content=data_bytes,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Hayate/0.1",
            },
        )
    # AniList rate limit: wait the advertised time once, then retry.
    if resp.status_code == 429 and not _retried:
        try:
            wait = min(int(resp.headers.get("Retry-After", "5")), 30)
        except ValueError:
            wait = 5
        time.sleep(wait)
        return _send_query(query, variables, _retried=True)
    resp.raise_for_status()
    result = resp.json()

    if result.get("errors") and not result.get("data"):
        msg = "; ".join(str(err.get("message", err)) for err in result["errors"])
        raise RuntimeError(f"AniList error: {msg}")
    return result


def _resolve_adult(allow_adult: Optional[bool]) -> bool:
    """None means "use the saved setting"."""
    if allow_adult is None:
        return bool(load_settings().get("allow_adult", False))
    return bool(allow_adult)


def _adult_arg(allow_adult: bool) -> str:
    return "" if allow_adult else "isAdult: false, genre_not_in: [\"Ecchi\", \"Hentai\"],"


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def _fuzzy_date(d: Optional[dict]):
    if not d:
        return None
    y, m, day = d.get("year"), d.get("month"), d.get("day")
    if not (y or m or day):
        return None
    return (y, m, day)


def _parse_media_node(item: dict) -> AnimeMedia:
    t = item.get("title", {}) or {}
    resolved_title = t.get("english") or t.get("romaji") or "Unknown Title"

    cover = (item.get("coverImage") or {}).get("large") or ""
    banner = item.get("bannerImage") or cover
    raw_eps = item.get("episodes")
    next_air = item.get("nextAiringEpisode")
    if not raw_eps and next_air and isinstance(next_air, dict):
        current_airing_ep = next_air.get("episode")
        if current_airing_ep and current_airing_ep > 1:
            raw_eps = current_airing_ep - 1

    parsed_relations = []
    edges = (item.get("relations") or {}).get("edges", [])
    for edge in edges:
        rel_type = edge.get("relationType", "")
        node = edge.get("node")
        if node:
            rel_t = node.get("title", {}) or {}
            node_title = rel_t.get("english") or rel_t.get("romaji") or "Unknown"
            parsed_relations.append({
                "relation_type": rel_type,
                "id": node.get("id"),
                "title": node_title,
                "format": node.get("format"),
                "cover_url": (node.get("coverImage") or {}).get("large", ""),
            })

    nxt = item.get("nextAiringEpisode") or {}

    return AnimeMedia(
        id=item.get("id"),
        title=resolved_title,
        cover_url=cover,
        banner_url=banner,
        description=item.get("description") or "",
        format=item.get("format"),
        episodes=raw_eps if raw_eps is not None else 1,
        average_score=item.get("averageScore"),
        season=item.get("season"),
        season_year=item.get("seasonYear"),
        genres=item.get("genres") or [],
        is_adult=bool(item.get("isAdult", False)),
        relations=parsed_relations,
        airing_episode=nxt.get("episode"),
        airing_time=nxt.get("airingAt"),
        start_date=_fuzzy_date(item.get("startDate")),
        end_date=_fuzzy_date(item.get("endDate")),
        status=item.get("status"),
    )


MEDIA_FIELDS = """
  id
  title {
    romaji
    english
  }
  coverImage {
    large
  }
  bannerImage
  description
  format
  episodes
  averageScore
  season
  seasonYear
  genres
  isAdult
  status
  startDate { year month day }
  endDate { year month day }
  nextAiringEpisode {
    episode
    airingAt
  }
  relations {
    edges {
      relationType
      node {
        id
        title {
          romaji
          english
        }
        format
        coverImage {
          large
        }
      }
    }
  }
"""


def _parse_list(raw: Optional[list], allow_adult: bool) -> List[AnimeMedia]:
    out = [_parse_media_node(i) for i in (raw or [])]
    if not allow_adult:
        out = [m for m in out if not m.is_adult]
    return out


def _dedupe(items: List[AnimeMedia], exclude_ids: set) -> List[AnimeMedia]:
    seen = set(exclude_ids)
    out = []
    for m in items:
        if m.id in seen:
            continue
        seen.add(m.id)
        out.append(m)
    return out


# --------------------------------------------------------------------------
# Search / details
# --------------------------------------------------------------------------

SEARCH_QUERY = f"""
query ($query: String, $page: Int, $perPage: Int) {{
  Page(page: $page, perPage: $perPage) {{
    pageInfo {{
      total
      perPage
      currentPage
      hasNextPage
    }}
    media(search: $query, type: ANIME, sort: [SEARCH_MATCH, POPULARITY_DESC]) {{
      {MEDIA_FIELDS}
    }}
  }}
}}
"""

ANIME_BY_ID_QUERY = f"""
query ($id: Int) {{
  Media(id: $id, type: ANIME) {{
    {MEDIA_FIELDS}
  }}
}}
"""


def search_anime_sync(query: str, page: int = 1, per_page: int = 35,
                      allow_adult: Optional[bool] = None) -> Tuple[List[AnimeMedia], bool]:
    """Returns (results, had_adult_hits). had_adult_hits is True when adult
    entries matched but were hidden by the user's setting."""
    allow = _resolve_adult(allow_adult)
    res_json = _send_query(SEARCH_QUERY, {"query": query, "page": page, "perPage": per_page})
    media_list = (res_json.get("data") or {}).get("Page", {}).get("media", []) or []
    everything = [_parse_media_node(item) for item in media_list]
    if allow:
        return everything, False
    kept = [m for m in everything if not m.is_adult]
    return kept, len(kept) != len(everything)


def search_many_sync(titles: List[str], allow_adult: Optional[bool] = None) -> List[Optional[AnimeMedia]]:
    """One AniList request for several titles: best match (or None) per title, same order."""
    allow = _resolve_adult(allow_adult)
    decl = ", ".join(f"$q{i}: String" for i in range(len(titles)))
    body = "\n".join(
        f"  a{i}: Page(page: 1, perPage: 1) {{ media(search: $q{i}, type: ANIME, "
        f"sort: [SEARCH_MATCH, POPULARITY_DESC]) {{ {MEDIA_FIELDS} }} }}" for i in range(len(titles)))
    res = _send_query(f"query ({decl}) {{\n{body}\n}}", {f"q{i}": t for i, t in enumerate(titles)})
    data = res.get("data") or {}
    out = []
    for i in range(len(titles)):
        nodes = (data.get(f"a{i}") or {}).get("media") or []
        m = _parse_media_node(nodes[0]) if nodes else None
        out.append(None if (m is not None and m.is_adult and not allow) else m)
    return out


def get_anime_by_id_sync(anime_id: int) -> Optional[AnimeMedia]:
    res_json = _send_query(ANIME_BY_ID_QUERY, {"id": anime_id})
    media = (res_json.get("data") or {}).get("Media")
    if not media:
        return None
    return _parse_media_node(media)


# --------------------------------------------------------------------------
# Home shelves
# --------------------------------------------------------------------------

UPCOMING_SHELF_LIMIT = 20
SHELF_LIMIT = 12
SPOTLIGHT_LIMIT = 10


def _home_query(allow_adult: bool) -> str:
    adult = _adult_arg(allow_adult)
    return f"""
query ($monthAgo: FuzzyDateInt) {{
  spotlight: Page(page: 1, perPage: 15) {{
    media(type: ANIME, status: RELEASING, sort: TRENDING_DESC, format_in: [TV, ONA], {adult}) {{
      {MEDIA_FIELDS}
    }}
  }}
  trending: Page(page: 1, perPage: 20) {{
    media(type: ANIME, sort: TRENDING_DESC, {adult}) {{
      {MEDIA_FIELDS}
    }}
  }}
  new_releases: Page(page: 1, perPage: 24) {{
    media(type: ANIME, status: RELEASING, sort: START_DATE_DESC, format_in: [TV, ONA], {adult}) {{
      {MEDIA_FIELDS}
    }}
  }}
  just_finished: Page(page: 1, perPage: 12) {{
    media(type: ANIME, status: FINISHED, sort: END_DATE_DESC, endDate_greater: $monthAgo, format_in: [TV, ONA], {adult}) {{
      {MEDIA_FIELDS}
    }}
  }}
  upcoming: Page(page: 1, perPage: 30) {{
    media(type: ANIME, status: NOT_YET_RELEASED, sort: POPULARITY_DESC, {adult}) {{
      {MEDIA_FIELDS}
    }}
  }}
}}
"""


def _fuzzy_int(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def build_shelves(data: dict, allow_adult: bool) -> Dict[str, List[AnimeMedia]]:
    """Turns the raw GraphQL payload into the shelves shown on the home screen."""
    def raw(key):
        return (data.get(key) or {}).get("media", [])

    # Spotlight: currently airing, trending, and has a real banner image.
    spotlight_all = _parse_list(raw("spotlight"), allow_adult)
    spotlight = [m for m in spotlight_all if m.banner_url and m.banner_url != m.cover_url]
    if len(spotlight) < 5:  # not enough banners, fall back to whatever we have
        spotlight = spotlight_all
    spotlight = spotlight[:SPOTLIGHT_LIMIT]
    spot_ids = {m.id for m in spotlight}

    # Trending skips whatever is already in the spotlight so the two never match.
    trending = _dedupe(_parse_list(raw("trending"), allow_adult), spot_ids)[:SHELF_LIMIT]

    popular = _dedupe(_parse_list(raw("popular"), allow_adult), set())[:SHELF_LIMIT]

    # New releases: first seasons only (no PREQUEL relation).
    new_releases = [m for m in _parse_list(raw("new_releases"), allow_adult) if not m.has_prequel][:SHELF_LIMIT]

    # Just finished: query already limits to the last 30 days, newest first.
    just_finished = _parse_list(raw("just_finished"), allow_adult)[:SHELF_LIMIT]

    # Upcoming: only entries with a confirmed premiere month.
    upcoming = [m for m in _parse_list(raw("upcoming"), allow_adult) if m.is_date_confirmed][:UPCOMING_SHELF_LIMIT]

    return {
        "spotlight": spotlight,
        "trending": trending,
        "popular": popular,
        "new_releases": new_releases,
        "just_finished": just_finished,
        "upcoming": upcoming,
    }


HOME_CACHE_TTL = 20 * 60  # seconds


def _home_cache_path():
    return cache_dir() / "home_cache.json"


def get_home_shelves_sync(allow_adult: Optional[bool] = None, use_cache: bool = True) -> Dict[str, List[AnimeMedia]]:
    allow = _resolve_adult(allow_adult)
    path = _home_cache_path()

    if use_cache:
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            fresh = time.time() - blob.get("fetched_at", 0) < HOME_CACHE_TTL
            if fresh and blob.get("allow_adult") == allow and blob.get("date") == date.today().isoformat():
                return build_shelves(blob["data"], allow)
        except Exception:
            pass

    month_ago = _fuzzy_int(date.today() - timedelta(days=30))
    res_json = _send_query(_home_query(allow), {"monthAgo": month_ago})
    data = res_json.get("data") or {}
    try:
        path.write_text(json.dumps({
            "fetched_at": time.time(),
            "allow_adult": allow,
            "date": date.today().isoformat(),
            "data": data,
        }), encoding="utf-8")
    except Exception:
        pass
    return build_shelves(data, allow)


def get_upcoming_sync(page: int = 1, per_page: int = 50,
                      allow_adult: Optional[bool] = None) -> Tuple[List[AnimeMedia], bool]:
    """Full upcoming list for a "see all" screen. Returns (items, has_next_page)."""
    allow = _resolve_adult(allow_adult)
    q = f"""
query ($page: Int, $perPage: Int) {{
  Page(page: $page, perPage: $perPage) {{
    pageInfo {{ hasNextPage }}
    media(type: ANIME, status: NOT_YET_RELEASED, sort: POPULARITY_DESC, {_adult_arg(allow)}) {{
      {MEDIA_FIELDS}
    }}
  }}
}}
"""
    res = _send_query(q, {"page": page, "perPage": per_page})
    page_data = (res.get("data") or {}).get("Page") or {}
    items = [m for m in _parse_list(page_data.get("media"), allow) if m.is_date_confirmed]
    return items, bool((page_data.get("pageInfo") or {}).get("hasNextPage"))


# --------------------------------------------------------------------------
# Weekly schedule
# --------------------------------------------------------------------------

SCHEDULE_QUERY = """
query ($start: Int, $end: Int, $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    airingSchedules(airingAt_greater: $start, airingAt_lesser: $end, sort: TIME) {
      airingAt
      episode
      media {
        id
        title { romaji english }
        coverImage { large }
        format
        isAdult
        episodes
      }
    }
  }
}
"""


def week_bounds(now: Optional[datetime] = None) -> Tuple[int, int]:
    """Unix timestamps for Monday 00:00 (local time) to the following Monday."""
    now = now or datetime.now()
    monday = datetime.combine(now.date() - timedelta(days=now.weekday()), datetime.min.time())
    next_monday = monday + timedelta(days=7)
    return int(monday.timestamp()), int(next_monday.timestamp())


def _schedule_cache_path():
    return config_dir() / "schedule_cache.json"


def _read_schedule_cache(week_start: int, allow_adult: bool):
    try:
        blob = json.loads(_schedule_cache_path().read_text(encoding="utf-8"))
        if (blob.get("week_start") == week_start
                and blob.get("allow_adult") == allow_adult
                and time.time() - blob.get("fetched_at", 0) < SCHEDULE_CACHE_TTL):
            return blob.get("data")
    except Exception:
        pass
    return None


def _write_schedule_cache(week_start: int, allow_adult: bool, data: dict):
    try:
        _schedule_cache_path().write_text(json.dumps({
            "week_start": week_start,
            "allow_adult": allow_adult,
            "fetched_at": time.time(),
            "data": data,
        }), encoding="utf-8")
    except Exception:
        pass


def build_schedule(entries: List[dict], week_start: int, allow_adult: bool) -> Dict[str, List[Dict[str, Any]]]:
    """Groups raw airingSchedules entries into local-time weekdays."""
    schedule: Dict[str, List[Dict[str, Any]]] = {day: [] for day in DAYS_OF_WEEK}
    seen = set()
    for e in entries:
        media = e.get("media") or {}
        if media.get("format") not in SCHEDULE_FORMATS:
            continue
        genres_lower = [g.lower() for g in (media.get("genres") or [])]
        if not allow_adult and (media.get("isAdult") or "ecchi" in genres_lower or "hentai" in genres_lower):
            continue
        key = (media.get("id"), e.get("episode"))
        if key in seen:
            continue
        seen.add(key)

        airing_at = e.get("airingAt")
        if airing_at is None:
            continue
        local = datetime.fromtimestamp(airing_at)
        t = media.get("title") or {}
        schedule[DAYS_OF_WEEK[local.weekday()]].append({
            "id": media.get("id"),
            "title": t.get("english") or t.get("romaji") or "Unknown",
            "episode": e.get("episode"),
            "airing_at": airing_at,
            "time": local.strftime("%H:%M"),
            "cover_url": (media.get("coverImage") or {}).get("large", ""),
            "format": media.get("format"),
            "is_adult": bool(media.get("isAdult")),
        })
    for items in schedule.values():
        items.sort(key=lambda x: x["airing_at"])
    return schedule


def get_weekly_schedule_sync(start_timestamp: Optional[int] = None,
                             allow_adult: Optional[bool] = None,
                             use_cache: bool = True) -> Dict[str, List[Dict[str, Any]]]:
    allow = _resolve_adult(allow_adult)
    if start_timestamp is None:
        week_start, week_end = week_bounds()
    else:
        week_start, week_end = start_timestamp, start_timestamp + 7 * 86400

    if use_cache:
        cached = _read_schedule_cache(week_start, allow)
        if cached:
            return cached

    entries: List[dict] = []
    # AniList caps pages at 50 items; a full week is typically 10-15 pages.
    for page in range(1, 21):
        res = _send_query(SCHEDULE_QUERY, {"start": week_start - 1, "end": week_end, "page": page})
        page_data = (res.get("data") or {}).get("Page") or {}
        entries.extend(page_data.get("airingSchedules") or [])
        if not (page_data.get("pageInfo") or {}).get("hasNextPage"):
            break
        time.sleep(0.3)

    data = build_schedule(entries, week_start, allow)
    _write_schedule_cache(week_start, allow, data)
    return data
