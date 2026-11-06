"""Check GitHub releases for a newer Hayate version."""
import json, re, urllib.request
from PyQt6.QtCore import QThread, pyqtSignal

REPO = "Piyush-1927/Hayate-Multi-Platform"
API = f"https://api.github.com/repos/{REPO}/releases/latest"


def current_version() -> str:
    try:
        from importlib.metadata import version
        return version("hayate")
    except Exception:
        pass
    try:
        import tomllib, pathlib
        for d in pathlib.Path(__file__).resolve().parents:
            f = d / "pyproject.toml"
            if f.exists():
                return tomllib.loads(f.read_text())["project"]["version"]
    except Exception:
        pass
    return "0.0.0"


def _parse(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def check_update(current: str | None = None):
    """Return (latest_tag, page_url, notes) if newer, None if up to date. Raises on network error."""
    current = current or current_version()
    req = urllib.request.Request(API, headers={"User-Agent": "Hayate-updater"})
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.load(r)
    tag = d.get("tag_name", "")
    if _parse(tag) > _parse(current):
        return tag, d.get("html_url", ""), d.get("body", "")
    return None


class UpdateChecker(QThread):
    # (status, tag, url, notes): status is "new", "latest" or "error"
    done = pyqtSignal(str, str, str, str)

    def run(self):
        try:
            res = check_update()
            self.done.emit(*(("new",) + res) if res else ("latest", "", "", ""))
        except Exception as e:
            self.done.emit("error", "", "", str(e))
