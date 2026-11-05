"""Read AniList forum threads for an anime (no login). Call from a worker thread."""
import json, time, urllib.request

API = "https://graphql.anilist.co"
TTL = 300
_cache = {}

Q_THREADS = """query($mid:Int){Page(perPage:25){threads(mediaCategoryId:$mid,sort:[REPLIED_AT_DESC]){id title replyCount siteUrl}}}"""
Q_COMMENTS = """query($tid:Int){Page(perPage:50){threadComments(threadId:$tid,sort:[ID]){
id comment(asHtml:true) likeCount createdAt siteUrl user{name avatar{medium}} childComments}}}"""


def _gql(query, variables):
    req = urllib.request.Request(
        API, json.dumps({"query": query, "variables": variables}).encode(),
        {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Hayate"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)["data"]["Page"]


def find_thread(anilist_id):
    threads = _gql(Q_THREADS, {"mid": int(anilist_id)})["threads"]
    return max(threads, key=lambda t: t["replyCount"]) if threads else None


def _flat(c, depth=0):
    u = c.get("user") or {}
    out = [{"user": u.get("name", "?"), "avatar": (u.get("avatar") or {}).get("medium"),
            "html": c.get("comment", ""), "likes": c.get("likeCount", 0),
            "created": c.get("createdAt", 0), "depth": depth}]
    for ch in c.get("childComments") or []:
        out += _flat(ch, depth + 1)
    return out


def get_comments(anilist_id):
    """-> {"title","url","comments":[{user,avatar,html,likes,created,depth}]} or None."""
    if anilist_id in _cache and time.time() - _cache[anilist_id][0] < TTL:
        return _cache[anilist_id][1]
    t = find_thread(anilist_id)
    res = None
    if t:
        raw = _gql(Q_COMMENTS, {"tid": t["id"]})["threadComments"]
        res = {"title": t["title"], "url": t["siteUrl"],
               "comments": [x for c in raw for x in _flat(c)]}
    _cache[anilist_id] = (time.time(), res)
    return res
