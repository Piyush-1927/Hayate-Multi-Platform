"""Keeps QThread workers alive until they have fully stopped.

A QThread destroyed while still running calls qFatal() -> abort() (SIGABRT).
Every background QThread should be started through run_worker().
"""
from PyQt6.QtCore import QThread

_LIVE: set = set()


def run_worker(worker: QThread) -> QThread:
    _LIVE.add(worker)

    def _release(w=worker):
        w.wait()  # finished was emitted; this only waits for the OS thread to exit
        _LIVE.discard(w)

    worker.finished.connect(_release)
    worker.start()
    return worker


def shutdown_workers(timeout_ms: int = 3000):
    for w in list(_LIVE):
        w.quit()
        if not w.wait(timeout_ms):
            w.terminate()
            w.wait()
    _LIVE.clear()
