import hashlib
import os
import threading
from pathlib import Path
from hayate.core.settings import cache_dir, load_settings
import certifi
import httpx
from hayate.core import net
from PyQt6.QtCore import QRunnable, pyqtSignal, pyqtSlot, QObject, QThreadPool, QCoreApplication
from PyQt6.QtGui import QImage, QPixmap

CACHE_DIR = cache_dir() / "img_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_cfg = {"on": True, "max": 300 * 1024 * 1024, "target": 225 * 1024 * 1024}


def _load_cfg():
    mb = 300
    try:
        s = load_settings()
        mb = max(25, int(s.get("cache_mb", 300)))
        _cfg["on"] = bool(s.get("cache_enabled", True))
    except Exception:
        pass
    _cfg["max"] = mb * 1024 * 1024
    _cfg["target"] = int(_cfg["max"] * 0.75)


_load_cfg()

_MEMORY_CACHE: dict[str, QPixmap] = {}
_MAX_MEMORY_ITEMS = 150

# Posters never expire by age. They are only removed when the cache is over
# MAX_CACHE_BYTES, oldest-used first, until it is back under TARGET_CACHE_BYTES.
_PRUNE_CHECK_EVERY_BYTES = 5 * 1024 * 1024
_prune_lock = threading.Lock()
_bytes_since_prune = 0


def _cache_path_for(url: str) -> Path:
    return CACHE_DIR / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.dat"


def prune_disk_cache():
    """Keep the cache under MAX_CACHE_BYTES. Least recently used posters go first,
    and posters currently on screen (held in the memory cache) are never removed."""
    global _bytes_since_prune
    if not _prune_lock.acquire(blocking=False):
        return  # another prune is already running
    try:
        _bytes_since_prune = 0
        if not _cfg["on"] or not CACHE_DIR.exists():
            return
        in_use = {_cache_path_for(u).name for u in list(_MEMORY_CACHE.keys())}
        files = []
        total_size = 0
        for entry in os.scandir(CACHE_DIR):
            if entry.is_file() and entry.name.endswith(".dat"):
                stat = entry.stat()
                files.append((entry.path, entry.name, stat.st_mtime, stat.st_size))
                total_size += stat.st_size

        if total_size <= _cfg["max"]:
            return

        files.sort(key=lambda x: x[2])  # oldest first
        for path, name, _, size in files:
            if name in in_use:
                continue
            try:
                os.remove(path)
                total_size -= size
                if total_size <= _cfg["target"]:
                    break
            except OSError:
                pass
    except Exception:
        pass
    finally:
        _prune_lock.release()


def _note_bytes_written(n: int):
    """Called after each new poster is saved; triggers a prune while the app runs."""
    global _bytes_since_prune
    _bytes_since_prune += n
    if _bytes_since_prune >= _PRUNE_CHECK_EVERY_BYTES:
        prune_disk_cache()


class ImageWorkerSignals(QObject):
    # Workers hand back a QImage (thread-safe). Conversion to QPixmap
    # happens on the GUI thread in _Delivery.deliver().
    loaded = pyqtSignal(QImage)


class ImageDownloadTask(QRunnable):
    """Runs on a pool thread. Never touches QPixmap. Always emits exactly once
    (a null QImage on failure) so the pending-delivery bookkeeping can clean up."""

    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self.signals = ImageWorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        img = self._fetch()
        self.signals.loaded.emit(img if img is not None else QImage())

    def _fetch(self):
        if not self.url:
            return None

        cache_path = _cache_path_for(self.url)

        if _cfg["on"] and cache_path.exists():
            try:
                cache_path.touch(exist_ok=True)
                img = QImage()
                if img.load(str(cache_path)):
                    return img
            except Exception:
                pass

        try:
            with net.client(timeout=7.0, verify=certifi.where()) as client:
                resp = client.get(self.url)
                if resp.status_code == 200:
                    img = QImage()
                    if img.loadFromData(resp.content):
                        try:
                            if _cfg["on"]:
                                cache_path.write_bytes(resp.content)
                                _note_bytes_written(len(resp.content))
                        except Exception:
                            pass
                        return img
        except Exception:
            pass
        return None


class _Delivery(QObject):
    """Lives on the GUI thread so the callback always runs there, no matter
    which thread called ImageManager.load()."""

    def __init__(self, url: str, callback, manager):
        super().__init__()
        self.url = url
        self.callback = callback
        self.manager = manager
        app = QCoreApplication.instance()
        if app is not None:
            self.moveToThread(app.thread())

    @pyqtSlot(QImage)
    def deliver(self, img: QImage):
        app = QCoreApplication.instance()
        if app is None:
            return
        try:
            if not img.isNull():
                pix = QPixmap.fromImage(img)
                _store_ram(self.url, pix)
                if callable(self.callback):
                    self.callback(pix)
        except (RuntimeError, ReferenceError):
            # Target Qt widget was deleted before image arrived
            pass
        except Exception:
            pass
        finally:
            if self.manager and hasattr(self.manager, "_pending"):
                self.manager._pending.discard(self)
            try:
                self.deleteLater()
            except Exception:
                pass


def _store_ram(url: str, pix: QPixmap):
    if len(_MEMORY_CACHE) >= _MAX_MEMORY_ITEMS:
        oldest_key = next(iter(_MEMORY_CACHE))
        _MEMORY_CACHE.pop(oldest_key, None)
    _MEMORY_CACHE[url] = pix


class ImageManager:
    _instance = None

    def __init__(self):
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(4)
        self.pool.start(prune_disk_cache)
        self._pending: set = set()

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = ImageManager()
        return cls._instance

    def load(self, url: str, on_loaded, priority: int = 0):
        if not url:
            return
        if url in _MEMORY_CACHE:
            on_loaded(_MEMORY_CACHE[url])
            return

        task = ImageDownloadTask(url)
        delivery = _Delivery(url, on_loaded, self)
        self._pending.add(delivery)  # keep alive until the image arrives
        task.signals.loaded.connect(delivery.deliver)
        self.pool.start(task, priority)

    def is_cached(self, url: str) -> bool:
        if not url:
            return False
        if url in _MEMORY_CACHE:
            return True
        return _cfg["on"] and _cache_path_for(url).exists()

    def clear_cache(self):
        """Purges memory cache and all disk cache files safely."""
        _MEMORY_CACHE.clear()
        with _prune_lock:
            if CACHE_DIR.exists():
                for entry in os.scandir(CACHE_DIR):
                    if entry.is_file():
                        try:
                            os.remove(entry.path)
                        except OSError:
                            pass
            CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def shutdown(self):
        try:
            self.pool.clear()
            self.pool.waitForDone(200)
        except Exception:
            pass

def _clear_disk():
    with _prune_lock:
        for e in os.scandir(CACHE_DIR):
            if e.is_file():
                try: os.remove(e.path)
                except OSError: pass


def apply_cache_settings():
    """Re-read settings: trim now if over the new limit, wipe disk files if disabled."""
    _load_cfg()
    QThreadPool.globalInstance().start(prune_disk_cache if _cfg["on"] else _clear_disk)


image_mgr = ImageManager.get()
