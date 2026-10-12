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
        return self.mark_watched(anime_id=anime_id, title=title, episode=episode, cover_url=cover_url)

    def mark_watched(self, anime_id: int, title: str, episode: int, cover_url: str = ""):
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

    def get_recent_history(self, limit: int = 10) -> list[dict]:
        items = list(self.data.values())
        items.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
        return items[:limit]

# Singleton instance
history_mgr = HistoryManager()
