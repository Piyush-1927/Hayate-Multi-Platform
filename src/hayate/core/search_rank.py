import re
import unicodedata
from difflib import SequenceMatcher

STOP = {"the", "a", "an", "of", "no", "wa", "to", "and", "in", "on", "ni", "wo"}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").lower()
    s = re.sub(r"[^a-z0-9\u3040-\u30ff\u4e00-\u9fff ]+", " ", s)
    return " ".join(s.split())


def tokens(q):
    return [t for t in norm(q).split() if t not in STOP and len(t) > 1]


def _close(k, w):
    if w.startswith(k):
        return True
    if len(k) < 4:
        return False
    return SequenceMatcher(None, k, w[:len(k) + 1]).ratio() >= 0.78


def typo_variants(q):
    toks = sorted(tokens(q), key=len, reverse=True)
    if not toks or len(toks[0]) < 4:
        return []
    t = toks[0]
    return [t[1:], t[:-1]]


def title_score(m, q):
    nq, toks = norm(q), tokens(q)
    names = [m.title] + list(getattr(m, "synonyms", None) or [])
    best = 0
    for raw in names:
        t = norm(raw)
        if not t:
            continue
        words = t.split()
        if t == nq:
            s = 1000
        elif t.startswith(nq):
            s = 800
        elif nq in t:
            s = 600
        else:
            hit = sum(1 for k in toks if any(_close(k, w) for w in words))
            if not hit:
                continue
            s = 100 + 300 * hit / len(toks)
            if hit == len(toks):
                s += 100
        best = max(best, s)
    return best


def merge_rank(q, groups):
    # group = (items, base_bonus, step[, require_match]). AniList's own order
    # earns a bonus so nothing it returned for the full query is dropped.
    best = {}
    for g in groups:
        items, base, step = g[0], g[1], g[2]
        need = g[3] if len(g) > 3 else False
        for i, m in enumerate(items):
            ts = title_score(m, q)
            if need and not ts:
                continue
            sc = ts + max(0, base - step * i) + (m.average_score or 0) / 100
            if m.id not in best or sc > best[m.id][0]:
                best[m.id] = (sc, m)
    return [m for _, m in sorted(best.values(), key=lambda x: -x[0])]
