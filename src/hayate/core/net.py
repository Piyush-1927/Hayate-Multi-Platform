"""Central network routing for Hayate.

When DNS-over-HTTPS or a proxy is active, every client (AniList, posters, yt-dlp, Qt, mpv)
connects through one loopback bridge, so DNS and proxy policy live in one place.
LAN / loopback targets always connect directly.

Settings (all optional):
  dns_mode:     "doh" (default) | "system"
  doh_url:      DoH JSON endpoint (default Cloudflare by IP, so no DNS bootstrap is needed)
  dns_fallback: true (default) - use system DNS if DoH is unreachable
  proxy_mode:   "off" | "custom" | "tor"
  proxy_url:    http / https / socks5 / socks5h URL (custom mode)
  tor_port:     Tor SOCKS port
With socks5h:// (and Tor, and HTTP proxies) the proxy resolves names itself; DoH is used for
direct connections and plain socks5://.
"""
import asyncio
import base64
import ipaddress
import socket
import struct
import threading
import time
import urllib.parse

import httpx

from hayate.core.settings import load_settings

_SCHEMES = ("http", "https", "socks5", "socks5h")
DEFAULT_DOH = "https://1.1.1.1/dns-query"
_cache = (-10.0, {})
_dns_cache = {}
_bridge_lock = threading.Lock()
_bridge_port = 0


class ProxyConfigError(OSError):
    pass


def is_local(host: str) -> bool:
    """LAN / loopback targets never go through DoH or a proxy."""
    if not host or host == "localhost" or host.endswith(".local"):
        return True
    try:
        a = ipaddress.ip_address(host)
        return a.is_private or a.is_loopback or a.is_link_local
    except ValueError:
        return False


# ---- settings (cached for 1s) ---------------------------------------------
def _s() -> dict:
    global _cache
    if time.monotonic() - _cache[0] > 1.0:
        try:
            d = load_settings() or {}
        except Exception:
            d = {}
        _cache = (time.monotonic(), d)
    return _cache[1]


def invalidate():
    """Force the next call to re-read settings (call after changing network keys)."""
    global _cache
    _cache = (-10.0, {})


def _tor_port() -> int:
    try:
        return int(_s().get("tor_port", 9050))
    except (TypeError, ValueError):
        return 9050


def proxy_url():
    """Upstream proxy URL or None. Raises ProxyConfigError if a proxy is enabled but
    misconfigured: we never silently fall back to a direct connection."""
    s = _s()
    mode = str(s.get("proxy_mode", "off"))
    if mode == "tor":
        return f"socks5h://127.0.0.1:{_tor_port()}"
    if mode == "custom":
        url = str(s.get("proxy_url", "")).strip()
        u = urllib.parse.urlsplit(url)
        try:
            u.port
        except ValueError:
            raise ProxyConfigError("Invalid proxy port")
        if u.scheme not in _SCHEMES or not u.hostname:
            raise ProxyConfigError("Invalid proxy URL (use http://, socks5:// or socks5h://host:port)")
        return url
    return None


def doh_enabled() -> bool:
    return str(_s().get("dns_mode", "doh")) == "doh"


def doh_url() -> str:
    return str(_s().get("doh_url") or DEFAULT_DOH)


def _fallback() -> bool:
    return bool(_s().get("dns_fallback", True))


def active() -> bool:
    return proxy_url() is not None or doh_enabled()


def detect_tor(timeout: float = 0.4):
    """Port of a running local Tor SOCKS listener (tor 9050 / Tor Browser 9150), else None."""
    for port in dict.fromkeys((_tor_port(), 9050, 9150)):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                return port
        except OSError:
            continue
    return None


def tor_reachable(timeout: float = 0.4) -> bool:
    return detect_tor(timeout) is not None


# ---- DNS-over-HTTPS -------------------------------------------------------
def _doh(host):
    ips, ttl = [], 60
    with httpx.Client(timeout=4.0, trust_env=False) as c:
        for rtype, code in (("A", 1), ("AAAA", 28)):
            r = c.get(doh_url(), params={"name": host, "type": rtype},
                      headers={"accept": "application/dns-json"})
            r.raise_for_status()
            for a in r.json().get("Answer") or []:
                if a.get("type") == code and a.get("data"):
                    ips.append(a["data"])
                    ttl = max(30, min(int(a.get("TTL", 60)), 3600))
            if ips:
                break
    if not ips:
        raise OSError(f"no DoH answer for {host}")
    return ips[:4], ttl


def resolve_all(host: str) -> list:
    """Blocking. IPs for host via DoH (if enabled), else/fallback system DNS."""
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    now = time.monotonic()
    hit = _dns_cache.get(host)
    if hit and hit[1] > now:
        return hit[0]
    ips, ttl = None, 60
    if doh_enabled():
        try:
            ips, ttl = _doh(host)
        except Exception as e:
            if not _fallback():
                raise OSError(f"DNS-over-HTTPS failed for {host}: {e}") from e
    if not ips:
        ips = list(dict.fromkeys(i[4][0] for i in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)))
    if len(_dns_cache) > 512:
        _dns_cache.clear()
    _dns_cache[host] = (ips, now + ttl)
    return ips


# ---- loopback bridge (HTTP proxy front, DoH / SOCKS5 / HTTP upstream) ------
async def _socks5(r, w, u, host, port):
    user = urllib.parse.unquote(u.username or "")
    pw = urllib.parse.unquote(u.password or "")
    w.write(b"\x05\x02\x00\x02" if user else b"\x05\x01\x00")
    m = await r.readexactly(2)
    if m[1] == 2 and user:
        ub, pb = user.encode(), pw.encode()
        w.write(b"\x01" + bytes([len(ub)]) + ub + bytes([len(pb)]) + pb)
        if (await r.readexactly(2))[1]:
            raise OSError("socks auth failed")
    elif m[1] != 0:
        raise OSError("socks method refused")
    if u.scheme == "socks5":  # socks5 resolves locally (via DoH); socks5h leaves DNS to the proxy
        host = (await asyncio.get_running_loop().run_in_executor(None, resolve_all, host))[0]
    try:
        ip = ipaddress.ip_address(host)
        addr = (b"\x01" if ip.version == 4 else b"\x04") + ip.packed
    except ValueError:
        hb = host.encode()
        addr = b"\x03" + bytes([len(hb)]) + hb
    w.write(b"\x05\x01\x00" + addr + struct.pack(">H", port))
    rep = await r.readexactly(4)
    if rep[1]:
        raise OSError(f"socks connect failed ({rep[1]})")
    n = {1: 4, 4: 16}.get(rep[3]) or (await r.readexactly(1))[0]
    await r.readexactly(n + 2)


async def _http_connect(r, w, u, host, port):
    hd = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
    if u.username:
        cred = f"{urllib.parse.unquote(u.username)}:{urllib.parse.unquote(u.password or '')}"
        hd += "Proxy-Authorization: Basic " + base64.b64encode(cred.encode()).decode() + "\r\n"
    w.write((hd + "\r\n").encode())
    status = (await r.readuntil(b"\r\n\r\n")).split(b"\r\n", 1)[0]
    if b" 200" not in status:
        raise OSError("proxy refused CONNECT")


async def _direct(host, port):
    ips = await asyncio.get_running_loop().run_in_executor(None, resolve_all, host)
    last = None
    for ip in ips:
        try:
            return await asyncio.wait_for(asyncio.open_connection(ip, port), 10)
        except OSError as e:
            last = e
    raise OSError(f"cannot connect to {host}:{port}: {last}")


async def _open(host, port):
    if is_local(host):
        return await asyncio.open_connection(host, port)
    up = proxy_url()  # may raise ProxyConfigError -> request fails (fail-closed)
    if not up:
        return await _direct(host, port)
    u = urllib.parse.urlsplit(up)
    socks = u.scheme.startswith("socks5")
    r, w = await asyncio.open_connection(u.hostname, u.port or (1080 if socks else 8080))
    try:
        await (_socks5 if socks else _http_connect)(r, w, u, host, port)
    except BaseException:
        w.close()
        raise
    return r, w


async def _pipe(r, w):
    try:
        while True:
            d = await r.read(65536)
            if not d:
                break
            w.write(d)
            await w.drain()
        if w.can_write_eof():
            w.write_eof()
    except Exception:
        pass


async def _handle(cr, cw):
    w = None
    try:
        head = await asyncio.wait_for(cr.readuntil(b"\r\n\r\n"), 15)
        line, _, rest = head.partition(b"\r\n")
        method, target, ver = line.decode("latin-1").split(" ", 2)
        if method.upper() == "CONNECT":
            host, _, port = target.rpartition(":")
            r, w = await asyncio.wait_for(_open(host.strip("[]"), int(port)), 25)
            cw.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        else:
            u = urllib.parse.urlsplit(target)
            if not u.hostname:
                raise OSError("bad request")
            r, w = await asyncio.wait_for(_open(u.hostname, u.port or 80), 25)
            path = (u.path or "/") + ("?" + u.query if u.query else "")
            hs = [x for x in rest.split(b"\r\n")
                  if x and not x.lower().startswith((b"proxy-connection", b"connection"))]
            w.write(f"{method} {path} {ver}\r\n".encode("latin-1") + b"\r\n".join(hs)
                    + b"\r\nConnection: close\r\n\r\n")
        await asyncio.gather(_pipe(cr, w), _pipe(r, cw))
    except Exception:
        try:
            cw.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        except Exception:
            pass
    finally:
        for x in (cw, w):
            if x is not None:
                try:
                    x.close()
                except Exception:
                    pass


def bridge_url() -> str:
    """Start the loopback bridge once (127.0.0.1, random port) and return its URL."""
    global _bridge_port
    with _bridge_lock:
        if not _bridge_port:
            ready = threading.Event()

            def run():
                global _bridge_port
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                srv = loop.run_until_complete(asyncio.start_server(_handle, "127.0.0.1", 0))
                _bridge_port = srv.sockets[0].getsockname()[1]
                ready.set()
                loop.run_forever()

            threading.Thread(target=run, daemon=True, name="hayate-net-bridge").start()
            if not ready.wait(5):
                raise OSError("network bridge failed to start")
    return f"http://127.0.0.1:{_bridge_port}"


def route_url():
    """Proxy URL clients should use (the bridge), or None for plain direct."""
    return bridge_url() if active() else None


# ---- per-client hooks -------------------------------------------------------
def client(**kw) -> httpx.Client:
    """httpx client following the routing settings. Not for LAN traffic."""
    kw.setdefault("proxy", route_url())
    return httpx.Client(**kw)


def ytdlp_args() -> list:
    u = route_url()
    return ["--proxy", u] if u else []


def mpv_options() -> dict:
    u = route_url()
    return {"http_proxy": u} if u else {}


def qt_proxy():
    from PyQt6.QtNetwork import QNetworkProxy
    T = QNetworkProxy.ProxyType
    u = route_url()
    if not u:
        return QNetworkProxy(T.NoProxy)
    p = urllib.parse.urlsplit(u)
    return QNetworkProxy(T.HttpProxy, p.hostname, p.port)
