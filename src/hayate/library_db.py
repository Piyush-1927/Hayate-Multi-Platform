import json, sqlite3, time
from pathlib import Path

DB_PATH = Path.home() / ".config" / "hayate" / "library.db"
STATUSES = ("watching", "plan_to_watch", "watch_later", "completed", "on_hold", "dropped")
COLS = ("anilist_id", "title", "cover_url", "total_eps", "status", "favorite", "added_at", "updated_at")

SCHEMA = """
CREATE TABLE IF NOT EXISTS library (
    anilist_id  INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    cover_url   TEXT,
    total_eps   INTEGER,
    status      TEXT CHECK (status IN ('watching','plan_to_watch','watch_later',
                                       'completed','on_hold','dropped')),
    favorite    INTEGER NOT NULL DEFAULT 0,
    added_at    REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_library_status ON library(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_library_fav ON library(favorite, updated_at DESC);
"""

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con

def set_entry(con, anilist_id, title, cover_url=None, total_eps=None,
              status=None, favorite=None):
    """Upsert. status/favorite of None keep existing values."""
    if status is not None and status not in STATUSES:
        raise ValueError(status)
    now = time.time()
    con.execute("""
        INSERT INTO library (anilist_id,title,cover_url,total_eps,status,favorite,added_at,updated_at)
        VALUES (?,?,?,?,?,COALESCE(?,0),?,?)
        ON CONFLICT(anilist_id) DO UPDATE SET
            title=excluded.title,
            cover_url=COALESCE(excluded.cover_url, cover_url),
            total_eps=COALESCE(excluded.total_eps, total_eps),
            status=COALESCE(?, status),
            favorite=COALESCE(?, favorite),
            updated_at=excluded.updated_at
    """, (anilist_id, title, cover_url, total_eps, status, favorite, now, now,
          status, favorite))
    con.commit()

def remove_entry(con, anilist_id):
    con.execute("DELETE FROM library WHERE anilist_id=?", (anilist_id,))
    con.commit()

def get_entry(con, anilist_id):
    return con.execute("SELECT * FROM library WHERE anilist_id=?", (anilist_id,)).fetchone()

def list_by_status(con, status, limit=50):
    return con.execute("SELECT * FROM library WHERE status=? ORDER BY updated_at DESC LIMIT ?",
                       (status, limit)).fetchall()

def list_favorites(con, limit=50):
    return con.execute("SELECT * FROM library WHERE favorite=1 ORDER BY updated_at DESC LIMIT ?",
                       (limit,)).fetchall()

def export_json(con, path):
    rows = [dict(r) for r in con.execute("SELECT * FROM library")]
    Path(path).write_text(json.dumps({"version": 1, "library": rows}, indent=2))

def import_json(con, path, replace=False):
    rows = json.loads(Path(path).read_text())["library"]
    now = time.time()
    clean = []
    for r in rows:  # validate everything before touching the DB
        if not isinstance(r.get("anilist_id"), int) or not r.get("title"):
            raise ValueError("bad entry: %r" % (r,))
        if r.get("status") not in (None, *STATUSES):
            raise ValueError("bad status: %r" % (r.get("status"),))
        clean.append({
            "anilist_id": r["anilist_id"], "title": r["title"],
            "cover_url": r.get("cover_url"), "total_eps": r.get("total_eps"),
            "status": r.get("status"), "favorite": int(bool(r.get("favorite", 0))),
            "added_at": r.get("added_at", now), "updated_at": r.get("updated_at", now),
        })
    with con:
        if replace:
            con.execute("DELETE FROM library")
        con.executemany(
            "INSERT OR REPLACE INTO library (%s) VALUES (%s)"
            % (",".join(COLS), ",".join(":" + c for c in COLS)), clean)

_con = None

def db():
    """Shared connection for the UI thread."""
    global _con
    if _con is None:
        _con = connect()
    return _con

def put(con, anilist_id, title, cover_url, total_eps, status, favorite):
    """Overwrite status/favorite exactly (None status clears it)."""
    if status is not None and status not in STATUSES:
        raise ValueError(status)
    now = time.time()
    con.execute("""
        INSERT INTO library (anilist_id,title,cover_url,total_eps,status,favorite,added_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(anilist_id) DO UPDATE SET
            title=excluded.title,
            cover_url=COALESCE(excluded.cover_url, cover_url),
            total_eps=COALESCE(excluded.total_eps, total_eps),
            status=excluded.status, favorite=excluded.favorite,
            updated_at=excluded.updated_at
    """, (anilist_id, title, cover_url, total_eps, status, int(bool(favorite)), now, now))
    con.commit()

def counts(con):
    c = {r["status"]: r["n"] for r in con.execute(
        "SELECT status, COUNT(*) AS n FROM library WHERE status IS NOT NULL GROUP BY status")}
    c["fav"] = con.execute("SELECT COUNT(*) FROM library WHERE favorite=1").fetchone()[0]
    return c

def search(con, key, q=""):
    where, args = ("favorite=1", []) if key == "fav" else ("status=?", [key])
    if q:
        where += " AND title LIKE ?"
        args.append("%" + q + "%")
    return con.execute("SELECT * FROM library WHERE %s ORDER BY updated_at DESC" % where, args).fetchall()
