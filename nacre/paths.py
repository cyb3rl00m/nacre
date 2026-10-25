"""Where Nacre keeps its files. Pure Python so the backend is testable without GTK."""

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_DIR.parent

INSTALLED_HELPER = "/usr/libexec/nacre/nacre-helper"
SOURCE_HELPER = SOURCE_ROOT / "helper" / "nacre-helper"

# Must match STATE_DIR in helper/nacre-helper
STATE_DIR = Path(os.environ.get("NACRE_STATE_DIR", "/var/lib/nacre"))
QUARANTINE_INDEX = STATE_DIR / "quarantine.json"


def _xdg(var, fallback):
    value = os.environ.get(var)
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / fallback


def data_dir():
    path = _xdg("XDG_DATA_HOME", ".local/share") / "nacre"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir():
    path = _xdg("XDG_CONFIG_HOME", ".config") / "nacre"
    path.mkdir(parents=True, exist_ok=True)
    return path


def helper_path():
    """Prefer the installed helper (matches the polkit policy); fall back to the source tree."""
    if os.path.exists(INSTALLED_HELPER):
        return INSTALLED_HELPER
    return str(SOURCE_HELPER)


def is_installed():
    return os.path.exists(INSTALLED_HELPER)


def style_path():
    return PACKAGE_DIR / "style.css"


def source_icon_dir():
    """Icon folder in the source tree, used when running without installing."""
    return SOURCE_ROOT / "data" / "icons"
