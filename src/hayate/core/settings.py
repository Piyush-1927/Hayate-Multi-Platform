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
    "quality": "best",
    "preferred_audio": "sub",  # "sub", "dub_eng", "dub_hin", or "any"
    "cache_enabled": True,
    "cache_mb": 300,
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
