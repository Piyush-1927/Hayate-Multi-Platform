import base64
import errno
import getpass
import hashlib
import hmac
import json
import http.server
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import unquote
import webbrowser


def get_local_ip() -> str:
    """Fetch machine's LAN IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Dummy connect to determine primary local network route
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


class RangeRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler supporting HTTP 206 Partial Content (Byte Ranges) for video seeking."""

    def translate_path(self, path: str) -> str:
        # Decode %20 and special characters properly
        path = unquote(path)
        return super().translate_path(path)

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass  # client closed the socket (seek / metadata probe) - not an error

    def send_head(self):
        """Handle Range requests cleanly for instant video seek."""
        path = self.translate_path(self.path)
        range_header = self.headers.get("Range")
        if os.path.isdir(path) or not range_header:
            return super().send_head()

        m = re.match(r"\s*bytes=(\d*)-(\d*)\s*$", range_header)
        if not m or not (m.group(1) or m.group(2)):
            return super().send_head()  # malformed / multi-range: serve whole file

        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None
        try:
            file_len = os.fstat(f.fileno()).st_size
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), file_len - 1) if m.group(2) else file_len - 1
            else:  # suffix range: last N bytes (players probe the mkv index this way)
                start = max(file_len - int(m.group(2)), 0)
                end = file_len - 1

            if start >= file_len or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{file_len}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                f.close()
                return None

            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_len}")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

            f.seek(start)
            return RangeFileWrapper(f, length)
        except Exception:
            f.close()
            raise

    gate = None  # LANMediaServer that owns this handler (set per server in start())

    def _authorized(self) -> bool:
        gate = self.gate
        if gate is None:
            return True
        status = gate.check_auth(self.headers.get("Authorization", ""), self.client_address[0])
        if status == 200:
            return True
        self.send_response(status)
        if status == 401:
            self.send_header("WWW-Authenticate", 'Basic realm="Hayate"')
            self.send_header("X-Hayate-Auth", gate.auth_mode)
        else:
            self.send_header("Retry-After", "60")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if self._authorized():
            super().do_GET()

    def do_HEAD(self):
        if self._authorized():
            super().do_HEAD()

    def log_message(self, format, *args):
        # Silence console log spam during video streaming
        pass


class RangeFileWrapper:
    """Wrapper that emits exactly the requested byte slice."""
    def __init__(self, file_obj, length: int):
        self.file_obj = file_obj
        self.remaining = length

    def read(self, size=-1):
        if self.remaining <= 0:
            return b""
        read_size = self.remaining if size < 0 else min(size, self.remaining)
        chunk = self.file_obj.read(read_size)
        self.remaining -= len(chunk)
        return chunk

    def close(self):
        self.file_obj.close()


LAN_PORT_MIN, LAN_PORT_MAX = 8000, 8080


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Salted scrypt hash, stored as 'salt_hex$digest_hex'."""
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}${digest.hex()}"


def _verify_hash(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def get_tailscale_ip() -> str | None:
    """Tailscale IPv4 (100.x.y.z) if the CLI is installed and connected, else None."""
    exe = shutil.which("tailscale")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=3,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    except Exception:
        return None
    ip = out.splitlines()[0].strip() if out else ""
    return ip if ip.startswith("100.") else None


def tailscale_installed() -> bool:
    return shutil.which("tailscale") is not None


def _ts_state(exe: str) -> str:
    """tailscaled BackendState (Running, Stopped, NeedsLogin...) or '' when the daemon is unreachable."""
    try:
        r = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=4)
        return str(json.loads(r.stdout or "{}").get("BackendState") or "")
    except Exception:
        return ""


_TS_LOGIN_URL = re.compile(r"https://\S*(?:login\.tailscale\.com|/a/|/register/)\S*")


def _ts_up(exe: str):
    """Run 'tailscale up'. Returns (kind, text): ok | login (browser opened) | denied | fail."""
    try:
        p = subprocess.Popen([exe, "up", "--timeout=15s"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
    except OSError as e:
        return "fail", str(e)
    out = []
    for line in p.stdout:
        out.append(line)
        m = _TS_LOGIN_URL.search(line)
        if m:
            try:
                webbrowser.open(m.group(0))
            except Exception:
                pass
            return "login", m.group(0)
    p.wait()
    text = "".join(out).strip()
    if p.returncode == 0:
        return "ok", text
    return ("denied" if "access denied" in text.lower() else "fail"), text


def ensure_tailscale():
    """Use Tailscale if it is up, otherwise start it. Returns (ip, '') or (None, reason).
    Blocking. First run only: polkit prompts (service, operator) and a browser login."""
    exe = shutil.which("tailscale")
    if not exe:
        return None, "Tailscale is not installed."
    ip = get_tailscale_ip()
    if ip:
        return ip, ""
    linux = sys.platform.startswith("linux")
    pk = shutil.which("pkexec") if linux else None
    if not _ts_state(exe) and linux and shutil.which("systemctl"):
        active = subprocess.run(["systemctl", "is-active", "--quiet", "tailscaled"]).returncode == 0
        if not active:
            if not pk:
                return None, "Tailscale service is not running. Run: sudo systemctl enable --now tailscaled"
            try:
                r = subprocess.run([pk, "systemctl", "enable", "--now", "tailscaled"],
                                   capture_output=True, timeout=120)
            except Exception:
                r = None
            if r is None or r.returncode != 0:
                return None, "Could not start the Tailscale service (permission prompt cancelled?)."
        for _ in range(16):  # wait for the daemon socket
            if _ts_state(exe):
                break
            time.sleep(0.5)
    kind, text = _ts_up(exe)
    if kind == "denied" and pk:
        try:
            subprocess.run([pk, exe, "set", f"--operator={getpass.getuser()}"],
                           capture_output=True, timeout=120)
        except Exception:
            pass
        kind, text = _ts_up(exe)
    if kind == "login":
        return None, "Finish the Tailscale login in your browser, then press Start again."
    for _ in range(10):
        ip = get_tailscale_ip()
        if ip:
            return ip, ""
        time.sleep(0.5)
    if kind == "denied":
        return None, "Tailscale needs permission to connect. Run once: sudo tailscale set --operator=$USER"
    return None, "Could not bring Tailscale up." + (f" {text.splitlines()[0]}" if text else "")


class _LANServer(http.server.ThreadingHTTPServer):
    # On Windows SO_REUSEADDR would let a second server share an already-used port
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


class LANMediaServer:
    """Settings keys (core/settings): lan_mode self|local|tailscale, lan_port 8000-8080,
    lan_auth none|password|pin, lan_pw_hash (see hash_password)."""

    def __init__(self, port: int = 8080):
        self.port = port
        self.host = ""
        self.mode = "local"
        self.auth_mode = "none"
        self.pin = ""            # temporary PIN, memory only, regenerated on every start
        self.notice = ""         # last warning/error for the UI
        self.port_fallback = False
        self.ip_visible = False  # shared by the Settings card and the Host Manager
        self.serving_dir = None
        self.server = None
        self.thread = None
        self.is_running = False
        self._lock = threading.Lock()
        self._pw_hash = ""
        self._ok_headers = set()  # sha256 of Authorization headers already verified (skips scrypt per request)
        self._fails = {}
        self._blocked = {}

    def _resolve_host(self, mode: str):
        if mode == "self":
            return "self", "127.0.0.1"
        if mode == "tailscale":
            ip, msg = ensure_tailscale()
            if ip:
                return "tailscale", ip
            self.notice = msg or "Tailscale is not available."
            return "tailscale", ""  # never fall back to Wi-Fi exposure when Internet LAN was chosen
        return "local", get_local_ip()

    def start(self, directory: Path) -> bool:
        from hayate.core.settings import load_settings
        self.stop()
        cfg = load_settings()
        self.notice = ""
        self.serving_dir = Path(directory).resolve()
        self.mode, self.host = self._resolve_host(cfg.get("lan_mode", "local"))
        if not self.host:
            return False

        # Self LAN is loopback-only, so it never needs a password; the setting is kept for the other modes
        self.auth_mode = "none" if self.mode == "self" else cfg.get("lan_auth", "none")
        self._pw_hash = cfg.get("lan_pw_hash", "") if self.auth_mode == "password" else ""
        if self.auth_mode == "password" and not self._pw_hash:
            self.notice = "Password protection is on but no password is set."
            return False  # fail closed instead of serving openly

        try:
            pref = int(cfg.get("lan_port", 8080))
        except (TypeError, ValueError):
            pref = 8080
        if not LAN_PORT_MIN <= pref <= LAN_PORT_MAX:
            pref = 8080

        owner = self
        serving = str(self.serving_dir)

        class DirectoryHandler(RangeRequestHandler):
            gate = owner

            def __init__(handler_self, *args, **kwargs):
                super().__init__(*args, directory=serving, **kwargs)

        candidates = list(range(pref, LAN_PORT_MAX + 1)) + list(range(LAN_PORT_MIN, pref))
        self.server = None
        port = pref
        for port in candidates:
            try:
                self.server = _LANServer((self.host, port), DirectoryHandler)
                break
            except OSError as e:
                if e.errno in (errno.EADDRINUSE, errno.EACCES):
                    continue
                self.notice = f"Cannot bind {self.host}: {e}"
                return False
        if self.server is None:
            self.notice = f"No free port in {LAN_PORT_MIN}-{LAN_PORT_MAX}."
            return False

        self.port = port
        self.port_fallback = port != pref
        if self.port_fallback:
            self.notice = (self.notice + " " if self.notice else "") + f"Port {pref} is busy - using {port}."
        if self.auth_mode == "pin":
            self.pin = f"{secrets.randbelow(10**6):06d}"
        self.server.timeout = 2.0
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.is_running = True
        return True

    def status_text(self) -> str:
        """The one status line both pages show; the address is masked unless ip_visible."""
        reveal = self.ip_visible
        host = self.host if (self.is_running and self.host) else get_local_ip()
        ip = host if reveal else "\u2022\u2022\u2022.\u2022\u2022\u2022.\u2022\u2022\u2022.\u2022\u2022\u2022"
        if self.is_running:
            text = f"Hosting at http://{ip}:{self.port}"
            if self.auth_mode == "pin" and self.pin:
                text += "  |  PIN: " + (self.pin if reveal else "\u2022" * 6)
        else:
            text = f"LAN IP: {ip} (Inactive)"
        if self.notice:
            text += "\n" + self.notice
        return text

    def stop(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
        self.server = None
        self.thread = None
        self.is_running = False
        self.pin = ""
        with self._lock:
            self._ok_headers.clear()
            self._fails.clear()
            self._blocked.clear()

    def check_auth(self, header: str, ip: str) -> int:
        """200 = allowed, 401 = credentials needed or wrong, 429 = temporarily locked out."""
        if self.auth_mode == "none":
            return 200
        now = time.monotonic()
        key = hashlib.sha256(header.encode()).digest()
        with self._lock:
            if self._blocked.get(ip, 0) > now:
                return 429
            if header and key in self._ok_headers:
                return 200
        password = ""
        if header[:6].lower() == "basic ":
            try:
                password = base64.b64decode(header[6:].strip()).decode("utf-8", "replace").partition(":")[2]
            except Exception:
                password = ""
        if self.auth_mode == "pin":
            ok = bool(password) and hmac.compare_digest(password.encode(), self.pin.encode())
        else:
            ok = bool(password) and _verify_hash(password, self._pw_hash)
        with self._lock:
            if ok:
                self._ok_headers.add(key)
                self._fails.pop(ip, None)
                return 200
            if header:  # a real attempt, not a browser's first credential-less request
                n = self._fails.get(ip, 0) + 1
                self._fails[ip] = n
                if n >= 5:
                    self._blocked[ip] = now + 60
                    self._fails.pop(ip, None)
        return 401


lan_server = LANMediaServer()
