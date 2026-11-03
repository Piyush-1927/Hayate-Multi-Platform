import json
from pathlib import Path
from PyQt6.QtCore import QStandardPaths

DEFAULTS = {
    "provider": "ytdlp",
    "channels": [
        # Muse Communication network
        "Muse Asia",
        "Muse India",
        # Ani-One (Medialink) network
        "Ani-One Asia",
        "Ani-One India",
        # Studio & Publisher official distributors
        "Crunchyroll Collection",
        "Crunchyroll India",
        "RetroCrush",
        "GundamInfo",
        "TMS Anime",
    ],
    "allow_adult": False,
    "catalogue_link": True,  # Catalogue: search + filters combine (off = using one clears the other)
    "quality": "best",
    "preferred_audio": "sub",  # "sub", "dub_eng", "dub_hin", "any_dub" or "custom:<name>"
    "custom_dub": "",  # user-defined extra dub language, e.g. "Tamil"
    "cache_enabled": True,
    "cache_mb": 300,
    "proxy_mode": "off",  # "off" | "custom" | "tor"
    "proxy_url": "",
    "tor_port": 9050,
    "lan_mode": "local",  # "self" | "local" | "tailscale"
    "lan_port": 8080,  # preferred port, 8000-8080
    "lan_auth": "none",  # "none" | "password" | "pin"
    "lan_pw_hash": "",  # salted scrypt hash, see core/lan_server.hash_password
}

def config_dir() -> Path:
    # Strict XDG Base Directory specification compliance
    import os
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config else Path.home() / ".config"
    d = base / "hayate"
    d.mkdir(parents=True, exist_ok=True)
    return d

def cache_dir() -> Path:
    import os
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    d = base / "hayate"
    d.mkdir(parents=True, exist_ok=True)
    return d

def load_settings() -> dict:
    data = dict(DEFAULTS)
    try:
        data.update(json.loads((config_dir() / "settings.json").read_text(encoding="utf-8")))
    except Exception:
        pass
    return data

def save_settings(data: dict) -> None:
    (config_dir() / "settings.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def audio_options(st=None):
    """(label, data) pairs for every audio dropdown; custom dub added when set."""
    st = st or load_settings()
    opts = [("Sub (Original)", "sub"), ("Eng Dub", "dub_eng"),
            ("Hindi Dub", "dub_hin"), ("Any Dub", "any_dub")]
    name = (st.get("custom_dub") or "").strip()
    if name:
        opts.append((f"{name} Dub", f"custom:{name}"))
    return opts
