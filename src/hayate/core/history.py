import json
import os
import time
from pathlib import Path
from hayate.core.settings import config_dir

def history_file() -> Path:
    return config_dir() / "history.json"

class HistoryManager:
    def __init__(self):
        self.file_path = history_file()
        self.data = self._load()

    def _load(self) -> dict:
        if not self.file_path.exists():
            return {}
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except Exception:
            # Unreadable/corrupt file: keep a copy so the history isn't silently lost.
            try:
                self.file_path.replace(self.file_path.with_suffix(".json.corrupt"))
            except Exception:
                pass
            return {}

    def _save(self):
        # Write to a temp file then swap it in, so a crash mid-write can't corrupt history.
        tmp = self.file_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            os.replace(tmp, self.file_path)
        except Exception as e:
            print(f"[history] Failed to save history: {e}")

    def record_watch(self, anime_id: int, title: str, episode: int, cover_url: str = "",
                     banner_url: str = "", total_episodes: int = 0, **kwargs):
        return self.mark_watched(anime_id=anime_id, title=title, episode=episode, cover_url=cover_url,
                                 banner_url=banner_url, total_episodes=total_episodes)

    def mark_watched(self, anime_id: int, title: str, episode: int, cover_url: str = "",
                     banner_url: str = "", total_episodes: int = 0):
        key = str(anime_id)
        entry = self.data.get(key, {
            "id": anime_id,
            "title": title,
            "cover_url": cover_url,
            "watched_episodes": [],
            "last_episode": episode,
            "updated_at": 0
        })

        if episode not in entry["watched_episodes"]:
            entry["watched_episodes"].append(episode)
            entry["watched_episodes"].sort()

        entry["last_episode"] = episode
        entry["title"] = title
        if cover_url:
            entry["cover_url"] = cover_url
        if banner_url:
            entry["banner_url"] = banner_url
        if total_episodes and total_episodes > 0:
            entry["total_episodes"] = int(total_episodes)
        entry["updated_at"] = int(time.time())

        self.data[key] = entry
        self._save()

    def get_watched_episodes(self, anime_id: int) -> set[int]:
        entry = self.data.get(str(anime_id))
        if entry:
            return set(entry.get("watched_episodes", []))
        return set()

    def get_next_episode(self, anime_id: int, total_episodes: int) -> int:
        entry = self.data.get(str(anime_id))
        if not entry:
            return 1
        last = entry.get("last_episode", 0)
        watched = set(entry.get("watched_episodes", []))
        
        # Suggest the next unwatched episode
        candidate = last + 1
        if candidate <= total_episodes and candidate not in watched:
            return candidate
            
        for ep in range(1, total_episodes + 1):
            if ep not in watched:
                return ep

        return min(last or 1, total_episodes)

    # ---- per-episode playback position ---------------------------------
    def set_progress(self, anime_id: int, episode: int, pos_s: float, dur_s: float):
        entry = self.data.get(str(anime_id))
        if not entry or dur_s <= 0 or pos_s < 0:
            return
        prog = entry.setdefault("progress", {})
        key = str(episode)
        if pos_s >= dur_s * 0.9:  # finished: drop the resume point, keep it watched
            if prog.pop(key, None) is not None:
                self._dirty = True
            watched = entry.setdefault("watched_episodes", [])
            if episode not in watched:
                watched.append(episode)
                watched.sort()
                self._dirty = True
        elif pos_s >= 10:
            prog[key] = {"pos": round(pos_s, 1), "dur": round(dur_s, 1)}
            self._dirty = True
        if getattr(self, "_dirty", False) and time.time() - getattr(self, "_last_flush", 0.0) >= 5:
            self.flush()

    def get_progress(self, anime_id: int, episode: int):
        """(position_s, duration_s) for a partly watched episode, else None."""
        p = ((self.data.get(str(anime_id)) or {}).get("progress") or {}).get(str(episode))
        if p and p.get("dur", 0) > 0 and p.get("pos", 0) > 0:
            return float(p["pos"]), float(p["dur"])
        return None

    def get_resume_episode(self, anime_id: int, total_episodes: int) -> int:
        entry = self.data.get(str(anime_id)) or {}
        last = entry.get("last_episode")
        if last and self.get_progress(anime_id, last):
            return int(last)
        return self.get_next_episode(anime_id, total_episodes)

    def flush(self):
        if getattr(self, "_dirty", False):
            self._dirty = False
            self._last_flush = time.time()
            self._save()

    def get_recent_history(self, limit: int = 10) -> list[dict]:
        items = list(self.data.values())
        items.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
        return items[:limit]

    # ---- export / import -------------------------------------------------
    FORMAT = "hayate-history"
    VERSION = 1

    def export_payload(self) -> dict:
        return {"format": self.FORMAT, "version": self.VERSION,
                "exported_at": int(time.time()), "history": self.data}

    @staticmethod
    def parse_backup(obj) -> dict:
        # Accepts the new envelope or a legacy bare {id: entry} dict.
        # Returns a cleaned {str(id): entry}; raises ValueError if unusable.
        if isinstance(obj, dict) and obj.get("format") == HistoryManager.FORMAT:
            obj = obj.get("history")
        if not isinstance(obj, dict):
            raise ValueError("file is not a Hayate history backup")
        clean = {}
        for k, v in obj.items():
            if not isinstance(v, dict):
                continue
            try:
                aid = int(v.get("id", k))
            except (TypeError, ValueError):
                continue
            eps = v.get("watched_episodes", [])
            if not isinstance(eps, list):
                continue
            eps = sorted({e for e in eps if isinstance(e, int) and not isinstance(e, bool)})
            try:
                last = int(v.get("last_episode", eps[-1] if eps else 0))
                upd = int(v.get("updated_at", 0) or 0)
            except (TypeError, ValueError):
                continue
            prog = {}
            raw = v.get("progress")
            if isinstance(raw, dict):
                for ek, ev in raw.items():
                    try:
                        pos, dur = float(ev["pos"]), float(ev["dur"])
                        int(ek)
                    except (TypeError, ValueError, KeyError):
                        continue
                    if pos > 0 and dur > 0:
                        prog[str(int(ek))] = {"pos": round(pos, 1), "dur": round(dur, 1)}
            clean[str(aid)] = {
                "id": aid,
                "title": str(v.get("title", "")),
                "cover_url": str(v.get("cover_url", "")),
                "watched_episodes": eps,
                "last_episode": last,
                "updated_at": upd,
                "total_episodes": v["total_episodes"] if isinstance(v.get("total_episodes"), int) and v["total_episodes"] > 0 else 0,
                "banner_url": str(v.get("banner_url", "")),
                "progress": prog,
            }
        if not clean:
            raise ValueError("no valid watch history entries found")
        return clean

    def merge(self, incoming: dict) -> tuple[int, int]:
        # Keeps current history; unions watched episodes; newer entry wins the metadata.
        added = updated = 0
        for key, new in incoming.items():
            cur = self.data.get(key)
            if cur is None:
                self.data[key] = new
                added += 1
                continue
            entry = dict(cur)
            entry["watched_episodes"] = sorted(set(cur.get("watched_episodes", [])) | set(new["watched_episodes"]))
            if new["updated_at"] > cur.get("updated_at", 0):
                for f in ("title", "cover_url", "last_episode", "updated_at"):
                    if new.get(f) not in ("", None):
                        entry[f] = new[f]
            prog = dict(cur.get("progress") or {})
            newer = new["updated_at"] > cur.get("updated_at", 0)
            for ek, ev in (new.get("progress") or {}).items():
                if newer or ek not in prog:
                    prog[ek] = ev
            done = set(entry["watched_episodes"])
            prog = {ek: ev for ek, ev in prog.items() if int(ek) not in done}
            if prog or "progress" in cur:
                entry["progress"] = prog
            if entry != cur:
                updated += 1
            self.data[key] = entry
        self._save()
        return added, updated

    def replace(self, incoming: dict):
        # Overwrites history, keeping a .bak of the previous file.
        try:
            if self.file_path.exists():
                self.file_path.with_suffix(".json.bak").write_text(
                    self.file_path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception as e:
            print(f"[history] Could not write .bak: {e}")
        self.data.clear()
        self.data.update(incoming)
        self._save()

    # ---- removal / full listing -----------------------------------------
    def _backup(self):
        try:
            if self.file_path.exists():
                self.file_path.with_suffix(".json.bak").write_text(
                    self.file_path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception as e:
            print(f"[history] Could not write .bak: {e}")

    def remove(self, anime_id: int) -> bool:
        key = str(anime_id)
        if key not in self.data:
            return False
        self._backup()
        del self.data[key]
        self._save()
        return True

    def clear(self):
        self._backup()
        self.data.clear()
        self._save()

    def get_all_history(self) -> list[dict]:
        items = list(self.data.values())
        items.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
        return items

# Singleton instance
history_mgr = HistoryManager()
