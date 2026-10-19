import os
from PyQt6.QtCore import QStandardPaths
from PyQt6.QtWidgets import QFileDialog


def downloads():
    d = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    return d if d and os.path.isdir(d) else os.path.expanduser("~")


def _start(a, k):
    a = list(a)
    cur = str(a[2] if len(a) > 2 else k.get("directory", ""))
    name = os.path.basename(cur) if os.path.splitext(cur)[1] else ""
    d = os.path.join(downloads(), name)
    if len(a) > 2: a[2] = d
    else: k["directory"] = d
    return a, k


def dlg_open(*a, **k):
    a, k = _start(a, k)
    return QFileDialog.getOpenFileName(*a, **k)


def dlg_save(*a, **k):
    a, k = _start(a, k)
    return QFileDialog.getSaveFileName(*a, **k)
