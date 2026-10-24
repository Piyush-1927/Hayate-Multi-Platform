"""Bring-your-own-cloud sync: history + library merged newest-wins via one JSON file."""
import json
import os
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from hayate.core.settings import config_dir

FORMAT = "hayate-sync"
FILE_NAME = "hayate_sync.json"
DEFAULT_CFG = {"backend": "off", "folder": ""}


def _cfg_path() -> Path:
    return config_dir() / "cloud.json"


def load_cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    try:
        cfg.update(json.loads(_cfg_path().read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_cfg(cfg: dict) -> None:
    p = _cfg_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)  # will hold tokens/passwords for later backends
    except OSError:
        pass
    tmp.replace(p)


# ---- backends: plain dict in/out, no Qt, no DB (safe on any thread) -------
def _folder_file(cfg) -> Path:
    raw = str(cfg.get("folder") or "").strip()
    folder = Path(raw).expanduser()
    if not raw or not folder.is_dir():
        raise ValueError("Sync folder doesn't exist. Choose an existing folder.")
    return folder / FILE_NAME


def read_remote(cfg):
    if cfg.get("backend") == "folder":
        f = _folder_file(cfg)
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            raise ValueError("The sync file in that folder is unreadable (left untouched).")
    raise ValueError("Cloud sync is off.")


def write_remote(cfg, payload: dict) -> None:
    if cfg.get("backend") == "folder":
        f = _folder_file(cfg)
        tmp = f.with_name(FILE_NAME + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
        return
    raise ValueError("Cloud sync is off.")


# ---- local side: main thread only (history_mgr + sqlite connection) -------
def build_payload() -> dict:
    from hayate import library_db as ldb
    from hayate.core.history import history_mgr
    return {"format": FORMAT, "version": 1, "saved_at": int(time.time()),
            "history": history_mgr.export_payload(),
            "library": ldb.export_payload(ldb.db())}


def merge_remote(remote: dict) -> int:
    from hayate import library_db as ldb
    from hayate.core.history import history_mgr, HistoryManager
    if remote.get("format") != FORMAT:
        raise ValueError("That file is not a Hayate sync file.")
    changed = 0
    try:
        a, u = history_mgr.merge(HistoryManager.parse_backup(remote.get("history")))
        changed += a + u
    except ValueError:
        pass  # remote has no usable history yet
    try:
        a, u = ldb.merge(ldb.db(), ldb.parse_backup(remote.get("library")))
        changed += a + u
    except (ValueError, KeyError, TypeError):
        pass  # remote has no usable library yet
    return changed


class SyncRunner(QObject):
    """pull (thread) -> merge (main thread) -> push (thread). Create on the main thread."""
    done = pyqtSignal(bool, str, int)  # ok, message, entries changed locally
    _pulled = pyqtSignal(object)
    _end = pyqtSignal(bool, str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._cfg = {}
        self._pulled.connect(self._merge_and_push)
        self._end.connect(self._finish)

    def start(self):
        if self._busy:
            return
        self._busy = True
        self._cfg = load_cfg()
        threading.Thread(target=self._pull_job, args=(self._cfg,), daemon=True).start()

    def _pull_job(self, cfg):
        try:
            self._pulled.emit(read_remote(cfg) or {})
        except Exception as e:
            self._end.emit(False, str(e), 0)

    def _merge_and_push(self, remote):
        try:
            changed = merge_remote(remote) if remote else 0
            payload = build_payload()
        except Exception as e:
            self._end.emit(False, f"Merge failed: {e}", 0)
            return
        threading.Thread(target=self._push_job, args=(self._cfg, payload, changed), daemon=True).start()

    def _push_job(self, cfg, payload, changed):
        try:
            write_remote(cfg, payload)
            self._end.emit(True, f"Synced. {changed} entries updated from the cloud.", changed)
        except Exception as e:
            self._end.emit(False, f"Upload failed: {e}", changed)

    def _finish(self, ok, msg, changed):
        self._busy = False
        self.done.emit(ok, msg, changed)
