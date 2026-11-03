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
DEFAULT_CFG = {"backend": "off", "webdav_url": "", "webdav_user": "",
               "webdav_pass": "", "auto": True,
               "google_refresh": "", "google_access": "", "google_expiry": 0, "google_email": ""}


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
def _dav(cfg):
    from urllib.parse import urlparse
    from hayate.core import net
    url = str(cfg.get("webdav_url") or "").strip().rstrip("/")
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Enter the full WebDAV folder URL, starting with https://")
    if u.scheme == "http" and not net.is_local(u.hostname):
        raise ValueError("Use https:// for a WebDAV server outside your network.")
    user = str(cfg.get("webdav_user") or "")
    auth = (user, str(cfg.get("webdav_pass") or "")) if user else None
    return net.client(timeout=20, auth=auth, follow_redirects=True), f"{url}/{FILE_NAME}"


def _dav_check(r):
    if r.status_code in (401, 403):
        raise ValueError("WebDAV login was rejected.")
    if r.status_code in (404, 409) and r.request.method == "PUT":
        raise ValueError("WebDAV folder not found. Create it first, then check the URL.")
    r.raise_for_status()


try:
    from hayate.core.google_secrets import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
except ImportError:
    import os
    GOOGLE_CLIENT_ID = os.environ.get("HAYATE_GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("HAYATE_GOOGLE_CLIENT_SECRET", "")
_G_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_G_TOKEN = "https://oauth2.googleapis.com/token"
_G_SCOPE = "openid email https://www.googleapis.com/auth/drive.appdata"
_G_FILES = "https://www.googleapis.com/drive/v3/files"
_G_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


def google_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def _gd_signin_blocking() -> str:
    """Browser sign-in (PKCE, loopback redirect). Blocks up to 3 min; returns the account email."""
    import base64, hashlib, secrets, webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlencode, urlparse, parse_qs
    from hayate.core import net

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    box = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            if u.path != "/":
                self.send_response(404); self.end_headers(); return
            q = parse_qs(u.query)
            box["code"] = (q.get("code") or [""])[0]
            box["state"] = (q.get("state") or [""])[0]
            box["error"] = (q.get("error") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body style='font-family:sans-serif;text-align:center;margin-top:20vh'>"
                             b"<h2>Hayate is connected.</h2><p>You can close this tab.</p></body></html>")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    srv.timeout = 1
    redirect = f"http://127.0.0.1:{srv.server_address[1]}"
    url = _G_AUTH + "?" + urlencode({
        "client_id": GOOGLE_CLIENT_ID, "redirect_uri": redirect, "response_type": "code",
        "scope": _G_SCOPE, "code_challenge": challenge, "code_challenge_method": "S256",
        "state": state, "access_type": "offline", "prompt": "consent"})
    try:
        webbrowser.open(url)
        end = time.time() + 180
        while time.time() < end and "code" not in box:
            srv.handle_request()
    finally:
        srv.server_close()
    if box.get("error") or not box.get("code"):
        raise ValueError("Sign-in was cancelled or timed out.")
    if box.get("state") != state:
        raise ValueError("Sign-in failed (state mismatch).")

    with net.client(timeout=20) as c:
        r = c.post(_G_TOKEN, data={
            "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "code": box["code"],
            "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect})
        if r.status_code != 200:
            raise ValueError(f"Google rejected the sign-in ({r.status_code}).")
        tok = r.json()
        email = ""
        try:
            e = c.get("https://openidconnect.googleapis.com/v1/userinfo",
                      headers={"Authorization": f"Bearer {tok['access_token']}"})
            email = e.json().get("email", "")
        except Exception:
            pass
    if not tok.get("refresh_token"):
        raise ValueError("Google didn't return a refresh token. Remove Hayate under your Google account's "
                         "third-party access, then sign in again.")
    cfg = load_cfg()
    cfg.update({"google_refresh": tok["refresh_token"], "google_access": tok["access_token"],
                "google_expiry": time.time() + int(tok.get("expires_in", 3600)) - 60,
                "google_email": email})
    save_cfg(cfg)
    return email


def _gd_token(cfg) -> str:
    from hayate.core import net
    if cfg.get("google_access") and float(cfg.get("google_expiry") or 0) > time.time():
        return cfg["google_access"]
    if not cfg.get("google_refresh"):
        raise ValueError("Sign in with Google first.")
    with net.client(timeout=20) as c:
        r = c.post(_G_TOKEN, data={
            "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": cfg["google_refresh"], "grant_type": "refresh_token"})
    if r.status_code in (400, 401):
        try:
            import os
            with open(os.path.expanduser("~/.config/hayate/auth_debug.log"), "a") as _f:
                _f.write(f"{time.ctime()} refresh failed {r.status_code} {r.text[:300]}\n")
        except Exception:
            pass
        raise ValueError("Google sign-in expired. Sign in again.")
    r.raise_for_status()
    t = r.json()
    cfg["google_access"] = t["access_token"]
    cfg["google_expiry"] = time.time() + int(t.get("expires_in", 3600)) - 60
    try:
        disk = load_cfg()
        disk["google_access"], disk["google_expiry"] = cfg["google_access"], cfg["google_expiry"]
        save_cfg(disk)
    except Exception:
        pass
    return cfg["google_access"]


def _gd_check(r):
    if r.status_code == 401:
        raise ValueError("Google sign-in expired. Sign in again.")
    if r.status_code == 403:
        raise ValueError("Google Drive refused access (is the Drive API enabled for this app?).")
    r.raise_for_status()


def _gd_find(c):
    r = c.get(_G_FILES, params={"spaces": "appDataFolder", "pageSize": 1, "fields": "files(id)",
                                "q": f"name = '{FILE_NAME}' and trashed = false"})
    _gd_check(r)
    f = r.json().get("files") or []
    return f[0]["id"] if f else None


def _gd_read(cfg):
    from hayate.core import net
    h = {"Authorization": f"Bearer {_gd_token(cfg)}"}
    with net.client(timeout=30, headers=h) as c:
        fid = _gd_find(c)
        if not fid:
            return None
        r = c.get(f"{_G_FILES}/{fid}", params={"alt": "media"})
        _gd_check(r)
        try:
            return r.json()
        except Exception:
            raise ValueError("The sync file in Google Drive is unreadable (left untouched).")


def _gd_write(cfg, payload):
    import secrets
    from hayate.core import net
    h = {"Authorization": f"Bearer {_gd_token(cfg)}"}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with net.client(timeout=60, headers=h) as c:
        fid = _gd_find(c)
        if fid:
            r = c.patch(f"{_G_UPLOAD}/{fid}", params={"uploadType": "media"}, content=body,
                        headers={"Content-Type": "application/json"})
        else:
            meta = json.dumps({"name": FILE_NAME, "parents": ["appDataFolder"]}).encode("utf-8")
            b = "hayate" + secrets.token_hex(8)
            mp = (f"--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + meta
                  + f"\r\n--{b}\r\nContent-Type: application/json\r\n\r\n".encode() + body
                  + f"\r\n--{b}--".encode())
            r = c.post(_G_UPLOAD, params={"uploadType": "multipart"}, content=mp,
                       headers={"Content-Type": f"multipart/related; boundary={b}"})
        _gd_check(r)


def read_remote(cfg):
    if cfg.get("backend") == "google":
        return _gd_read(cfg)
    if cfg.get("backend") == "webdav":
        c, url = _dav(cfg)
        with c:
            r = c.get(url)
        if r.status_code == 404:
            return None
        _dav_check(r)
        try:
            return r.json()
        except Exception:
            raise ValueError("The sync file on the WebDAV server is unreadable (left untouched).")
    raise ValueError("Cloud sync is off.")


def write_remote(cfg, payload: dict) -> None:
    if cfg.get("backend") == "google":
        _gd_write(cfg, payload)
        return
    if cfg.get("backend") == "webdav":
        c, url = _dav(cfg)
        with c:
            r = c.put(url, content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
        _dav_check(r)
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
        self._again = False
        self._pending_quiet = True
        self.quiet = False
        self._pulled.connect(self._merge_and_push)
        self._end.connect(self._finish)

    def start(self, quiet=False):
        if self._busy:
            self._pending_quiet = quiet if not self._again else (self._pending_quiet and quiet)
            self._again = True
            return
        self._busy = True
        self.quiet = quiet
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
        if self._again:
            self._again = False
            self.start(self._pending_quiet)


_runner = None


def get_runner():
    """One shared runner for the whole app (first call must be on the main thread)."""
    global _runner
    if _runner is None:
        from PyQt6.QtCore import QCoreApplication
        _runner = SyncRunner(QCoreApplication.instance())
    return _runner


def sync_on_quit(timeout: float = 8.0) -> None:
    """Blocking pull -> merge -> push for app exit. Each network step is bounded; never raises."""
    try:
        cfg = load_cfg()
        if cfg.get("backend", "off") == "off" or not cfg.get("auto", True):
            return
        if needs_link():
            return
        from hayate.core.history import history_mgr
        history_mgr.flush()
        box = {}

        def _pull():
            try:
                box["remote"] = read_remote(cfg) or {}
            except Exception as e:
                box["err"] = e

        t = threading.Thread(target=_pull, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive() or "err" in box:
            return
        if box.get("remote"):
            merge_remote(box["remote"])
            history_mgr.flush()
        payload = build_payload()

        def _push():
            try:
                write_remote(cfg, payload)
            except Exception as e:
                print(f"[cloud] quit sync upload failed: {e}")

        t2 = threading.Thread(target=_push, daemon=True)
        t2.start()
        t2.join(timeout)
    except Exception as e:
        print(f"[cloud] quit sync skipped: {e}")


def link_id(cfg=None) -> str:
    cfg = cfg if cfg is not None else load_cfg()
    b = cfg.get("backend", "off")
    if b == "google":
        return "google:" + str(cfg.get("google_email") or "account")
    if b == "webdav":
        return "webdav:" + "".join(str(v).strip().rstrip("/") for k, v in sorted(cfg.items()) if "url" in k.lower())
    return ""


def needs_link() -> bool:
    """True when this cloud account/server hasn't been through the connect-time choice yet."""
    cfg = load_cfg()
    if cfg.get("backend", "off") == "off":
        return False
    if "link_v" not in cfg:  # one-time: setups made before this feature count as already chosen
        cfg["link_v"] = 1
        if (cfg["backend"] == "google" and cfg.get("google_refresh")) or cfg["backend"] == "webdav":
            cfg["linked"] = link_id(cfg)
        save_cfg(cfg)
        return False
    return cfg.get("linked") != link_id(cfg)


def mark_linked() -> None:
    cfg = load_cfg()
    cfg["link_v"] = 1
    cfg["linked"] = link_id(cfg)
    save_cfg(cfg)


def unlink() -> None:
    cfg = load_cfg()
    cfg["link_v"] = 1
    cfg.pop("linked", None)
    save_cfg(cfg)


def _has(section, key) -> bool:
    if isinstance(section, dict):
        inner = section.get(key, section)
        if isinstance(inner, dict):
            return any(k not in ("format", "version", "exported_at", "saved_at") for k in inner)
        return bool(inner)
    return bool(section)


def remote_has_data(remote) -> bool:
    return (isinstance(remote, dict) and remote.get("format") == FORMAT
            and (_has(remote.get("history"), "history") or _has(remote.get("library"), "library")))


def local_has_data() -> bool:
    try:
        p = build_payload()
        return _has(p.get("history"), "history") or _has(p.get("library"), "library")
    except Exception:
        return False


def replace_local(remote: dict) -> None:
    """Make this device's history and library exactly the cloud's (history keeps a .bak)."""
    from hayate import library_db as ldb
    from hayate.core.history import history_mgr, HistoryManager
    if remote.get("format") != FORMAT:
        raise ValueError("That file is not a Hayate sync file.")
    try:
        history_mgr.replace(HistoryManager.parse_backup(remote.get("history")))
    except ValueError:
        pass
    try:
        ldb.replace(ldb.db(), ldb.parse_backup(remote.get("library")))
    except (ValueError, KeyError, TypeError):
        pass


class LinkProbe(QObject):
    """Reads the cloud copy off the UI thread for the connect-time choice; can upload local data once."""
    ready = pyqtSignal(object, str)   # remote dict ({} when none), error text
    pushed = pyqtSignal(bool, str)
    _got = pyqtSignal(object, str)
    _sent = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._got.connect(self.ready)
        self._sent.connect(self.pushed)

    def start(self):
        cfg = load_cfg()

        def job():
            try:
                self._got.emit(read_remote(cfg) or {}, "")
            except Exception as e:
                self._got.emit({}, str(e))
        threading.Thread(target=job, daemon=True).start()

    def push_local(self):
        cfg = load_cfg()
        payload = build_payload()

        def job():
            try:
                write_remote(cfg, payload)
                self._sent.emit(True, "Cloud data replaced with this device's data.")
            except Exception as e:
                self._sent.emit(False, f"Upload failed: {e}")
        threading.Thread(target=job, daemon=True).start()


def autosync(quiet=True):
    """Sync if a backend is on and auto-sync is enabled. Never raises."""
    try:
        cfg = load_cfg()
        if cfg.get("backend", "off") != "off" and cfg.get("auto", True) and not needs_link():
            get_runner().start(quiet)
    except Exception as e:
        print(f"[cloud] autosync skipped: {e}")


class GoogleSignIn(QObject):
    """Runs the browser sign-in off the UI thread. Create on the main thread."""
    finished = pyqtSignal(bool, str)  # ok, email or error text
    _res = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self._res.connect(self._fin)

    def start(self):
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self._job, daemon=True).start()

    def _job(self):
        try:
            self._res.emit(True, _gd_signin_blocking())
        except Exception as e:
            self._res.emit(False, str(e))

    def _fin(self, ok, msg):
        self.busy = False
        self.finished.emit(ok, msg)


def google_sign_out() -> None:
    cfg = load_cfg()
    token = cfg.get("google_refresh") or cfg.get("google_access")
    for k in ("google_refresh", "google_access", "google_email"):
        cfg[k] = ""
    cfg["google_expiry"] = 0
    save_cfg(cfg)

