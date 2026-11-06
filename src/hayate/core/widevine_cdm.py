"""Fetch the Widevine CDM without any browser installed.
Primary source: Mozilla's GMP update feed (what Firefox uses; points at Google's CDN).
Fallback: Google's component updater."""
import hashlib, io, json, os, platform, shutil, struct, sys, zipfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

GOOGLE_APP_ID = "oimompecagnajdejgnnjijobebaeigek"
GOOGLE_URL = "https://update.googleapis.com/service/update2/json"
MOZ_URL = ("https://aus5.mozilla.org/update/3/GMP/140.0/20250601000000/"
           "{target}/en-US/release/default/default/default/update.xml")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0.0.0"}


def _log(*a, file=None):
    try:
        d = cache_dir().parent
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "widevine.log", "a", encoding="utf-8") as f:
            f.write(" ".join(str(x) for x in a) + "\n")
    except Exception:
        pass
    try:
        print(*a, file=file)
    except Exception:
        pass


def _is_arm():
    return platform.machine().lower() in ("arm64", "aarch64")


def _plat():
    """(os key, arch, platform dir inside CRX, library file name, mozilla target)"""
    arm = _is_arm()
    arch = "arm64" if arm else "x64"
    if sys.platform == "darwin":
        return ("mac", arch, f"mac_{arch}", "libwidevinecdm.dylib",
                "Darwin_aarch64-gcc3" if arm else "Darwin_x86_64-gcc3")
    if sys.platform.startswith("win"):
        return ("win", arch, f"win_{arch}", "widevinecdm.dll",
                "WINNT_aarch64-msvc-aarch64" if arm else "WINNT_x86_64-msvc")
    return ("linux", arch, f"linux_{arch}", "libwidevinecdm.so",
            "Linux_aarch64-gcc3" if arm else "Linux_x86_64-gcc3")


def cache_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "hayate" / "widevine"


def _get(url, data=None, headers=None, timeout=60):
    h = dict(UA)
    h.update(headers or {})
    return urllib.request.urlopen(
        urllib.request.Request(url, data, h), timeout=timeout).read()


def _query_mozilla():
    target = _plat()[4]
    raw = _get(MOZ_URL.format(target=target))
    if os.environ.get("WIDEVINE_DEBUG"):
        _log(raw.decode(errors="replace"), file=sys.stderr)
    root = ET.fromstring(raw)
    for a in root.iter("addon"):
        if a.get("id") == "gmp-widevinecdm":
            return (a.get("version", "unknown"), a.get("URL"),
                    a.get("hashFunction", "sha512"), a.get("hashValue"))
    raise RuntimeError(f"no widevine addon in Mozilla feed: {raw[:300]!r}")


def _query_google():
    osn, arch = _plat()[0], _plat()[1]
    arm = arch == "arm64"
    os_name = {"linux": "Linux", "mac": "Mac OS X", "win": "Windows"}[osn]
    os_ver = {"linux": "6.8.0", "mac": "14.5.0", "win": "10.0.22631"}[osn]
    body = {"request": {
        "@os": osn, "@updater": "chrome", "acceptformat": "crx3",
        "app": [{"appid": GOOGLE_APP_ID, "installsource": "ondemand",
                 "updatecheck": {}, "version": "0.0.0.0"}],
        "arch": arch, "dedup": "cr", "hw": {"physmemory": 16},
        "nacl_arch": "arm64" if arm else "x86-64",
        "os": {"arch": "arm64" if arm else "x86_64",
               "platform": os_name, "version": os_ver},
        "prodversion": "130.0.6723.92", "protocol": "3.1",
        "updaterversion": "130.0.6723.92"}}
    raw = _get(GOOGLE_URL, json.dumps(body).encode(),
               {"Content-Type": "application/json"}).decode()
    if raw.startswith(")]}'"):
        raw = raw.split("\n", 1)[1]
    uc = json.loads(raw)["response"]["app"][0]["updatecheck"]
    if uc.get("status") != "ok":
        raise RuntimeError(f"google status={uc.get('status')}")
    pkg = uc["manifest"]["packages"]["package"][0]
    return (uc["manifest"]["version"], uc["urls"]["url"][0]["codebase"] + pkg["name"],
            "sha256", pkg["hash_sha256"])


def _alt_urls(url):
    out = [url]
    tail = url.rsplit("/", 1)[-1]
    for base in ("https://dl.google.com/widevine-cdm/",
                 "https://dl.google.com/dl/edgedl/widevine-cdm/",
                 "https://redirector.gvt1.com/edgedl/widevine-cdm/"):
        if base + tail not in out:
            out.append(base + tail)
    return out


def _install_from_chrome_deb() -> Path:
    import tarfile
    url = "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"
    _log("[widevine] downloading Chrome .deb (~110 MB) to extract the CDM...",
          file=sys.stderr)
    deb = _get(url, timeout=900)
    if deb[:8] != b"!<arch>\n":
        raise RuntimeError("deb is not an ar archive")
    pos, tar_bytes, tar_name = 8, None, ""
    while pos + 60 <= len(deb):
        name = deb[pos:pos + 16].decode().strip()
        size = int(deb[pos + 48:pos + 58].decode().strip())
        body = pos + 60
        if name.startswith("data.tar"):
            tar_bytes, tar_name = deb[body:body + size], name
            break
        pos = body + size + (size % 2)
    if tar_bytes is None:
        raise RuntimeError("data.tar not found in deb")
    if tar_name.endswith(".zst"):
        raise RuntimeError("deb uses zstd compression (unsupported here)")
    lib = man = None
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tf:
        for m in tf:
            if m.name.endswith("WidevineCdm/_platform_specific/linux_x64/libwidevinecdm.so"):
                lib = tf.extractfile(m).read()
            elif m.name.endswith("WidevineCdm/manifest.json"):
                man = tf.extractfile(m).read()
    if not lib:
        raise RuntimeError("libwidevinecdm.so not found in deb")
    version = json.loads(man)["version"] if man else "chrome-deb"
    out = cache_dir() / version
    out.mkdir(parents=True, exist_ok=True)
    (out / "libwidevinecdm.so").write_bytes(lib)
    os.chmod(out / "libwidevinecdm.so", 0o755)
    if man:
        (out / "manifest.json").write_bytes(man)
    return out / "libwidevinecdm.so"


def _install(version, url, hash_fn, hash_val) -> Path:
    lib_name, plat_dir = _plat()[3], _plat()[2]
    data = None
    for u in _alt_urls(url):
        try:
            data = _get(u, timeout=180)
            break
        except Exception as e:
            _log(f"[widevine] {u} -> {e}", file=sys.stderr)
    if data is None:
        raise RuntimeError("all download URLs failed")
    if hash_val and hashlib.new(hash_fn, data).hexdigest().lower() != hash_val.lower():
        raise RuntimeError("Widevine download hash mismatch")
    if data[:4] == b"Cr24":  # CRX3 wrapper: strip header, rest is a zip
        data = data[12 + struct.unpack("<I", data[8:12])[0]:]
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    member = (next((n for n in names if n.endswith(f"{plat_dir}/{lib_name}")), None)
              or next((n for n in names if n.split("/")[-1] == lib_name), None))
    if not member:
        raise RuntimeError(f"{lib_name} not in package: {names}")
    out = cache_dir() / version
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    (tmp / lib_name).write_bytes(zf.read(member))
    if "manifest.json" in names:
        (tmp / "manifest.json").write_bytes(zf.read("manifest.json"))
    if sys.platform != "win32":
        os.chmod(tmp / lib_name, 0o755)
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    return out / lib_name


def _cached():
    lib_name = _plat()[3]
    root = cache_dir()
    if not root.exists():
        return None
    key = lambda p: [int(x) if x.isdigit() else 0 for x in p.name.split(".")]
    for d in sorted((p for p in root.iterdir()
                     if p.is_dir() and not p.name.endswith(".tmp")),
                    key=key, reverse=True):
        if (d / lib_name).exists():
            return d / lib_name
    return None


def get_cdm_path(update: bool = False):
    """Path to the CDM library; downloads on first use. Falls back to cache offline."""
    have = _cached()
    if have and not update:
        return have
    for name, query in (("mozilla", _query_mozilla), ("google", _query_google)):
        try:
            version, url, fn, val = query()
            target = cache_dir() / version / _plat()[3]
            return target if target.exists() else _install(version, url, fn, val)
        except Exception as e:
            _log(f"[widevine] {name} source failed: {e}", file=sys.stderr)
    if sys.platform.startswith("linux") and not _is_arm():
        try:
            return _install_from_chrome_deb()
        except Exception as e:
            _log(f"[widevine] chrome-deb source failed: {e}", file=sys.stderr)
    return have


if __name__ == "__main__":
    _log(get_cdm_path(update="--update" in sys.argv))
