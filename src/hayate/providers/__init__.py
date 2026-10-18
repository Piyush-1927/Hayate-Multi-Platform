import sys
import inspect
import importlib.util
from pathlib import Path
from hayate.core.settings import config_dir
from hayate.providers.base import StreamProvider, Stream


def _is_candidate(cls, mod) -> bool:
    return (
        inspect.isclass(cls)
        and issubclass(cls, StreamProvider)
        and cls is not StreamProvider
        and cls.__module__ == mod.__name__
    )


def _validate(inst: StreamProvider) -> str:
    if not getattr(inst, "name", None):
        return "missing 'name' attribute"
    if not getattr(inst, "label", None):
        return "missing 'label' attribute"
    return ""


def load_providers() -> dict:
    """Loads built-in providers, user drop-in plugins from ~/.config/hayate/providers/,

    and falls back to PseudoProvider if no real provider is available.
    """
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("hayate_plugin_") or mod_name.startswith("hayate_builtin_"):
            del sys.modules[mod_name]

    found = {}

    # 1. Load Bundled Built-ins (e.g. ytdlp)
    builtin_dir = Path(__file__).parent / "builtins"
    if builtin_dir.is_dir():
        for path in sorted(builtin_dir.glob("*.py")):
            if path.name.startswith("__") or path.stem == "pseudo_provider":
                continue
            mod_name = f"hayate_builtin_{path.stem}"
            try:
                spec = importlib.util.spec_from_file_location(mod_name, path)
                mod = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = mod
                spec.loader.exec_module(mod)
                for _, cls in inspect.getmembers(mod, inspect.isclass):
                    if _is_candidate(cls, mod):
                        inst = cls()
                        if not _validate(inst):
                            found[inst.name] = inst
            except Exception as e:
                print(f"[providers] failed loading built-in {path.name}: {e}", file=sys.stderr)

    # 1b. Frozen (PyInstaller) builds ship no .py files: import built-ins by name instead
    if getattr(sys, "frozen", False) and not found:
        try:
            from hayate.providers.builtins import ytdlp_provider as _yt
            for _, cls in inspect.getmembers(_yt, inspect.isclass):
                if _is_candidate(cls, _yt):
                    inst = cls()
                    if not _validate(inst):
                        found[inst.name] = inst
        except Exception as e:
            print(f"[providers] failed loading frozen built-in ytdlp: {e}", file=sys.stderr)

    # 2. Load User Plugins from ~/.config/hayate/providers/ (User plugins take precedence)
    user_plugin_dir = config_dir() / "providers"
    user_plugin_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(user_plugin_dir.glob("*.py")):
        if path.name.startswith("__"):
            continue
        mod_name = f"hayate_plugin_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
            for _, cls in inspect.getmembers(mod, inspect.isclass):
                if _is_candidate(cls, mod):
                    inst = cls()
                    if not _validate(inst):
                        found[inst.name] = inst
        except Exception as e:
            print(f"[providers] failed loading user plugin {path.name}: {e}", file=sys.stderr)

    # 3. If zero real providers exist, register the PseudoProvider fallback
    if not found:
        from hayate.providers.builtins.pseudo_provider import PseudoProvider
        fallback = PseudoProvider()
        found[fallback.name] = fallback

    return found


def provider_label(name: str) -> str:
    """Returns human-friendly label. If pseudo or unknown, returns 'No Provider Installed (none)'."""
    if not name or name == "pseudo":
        return "No Provider Installed (none)"
    providers = load_providers()
    if name in providers:
        p = providers[name]
        return getattr(p, "label", name)
    return "No Provider Installed (none)"


def get_default_provider_name() -> str:
    providers = load_providers()
    # Any real provider has priority over pseudo
    for key in providers:
        if key != "pseudo":
            return key
    return "pseudo"
