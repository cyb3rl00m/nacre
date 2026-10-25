"""User settings and small bits of persistent state (last scan)."""

import json
import os

from . import paths

DEFAULTS = {
    "scan_archives": True,
    "detect_pua": False,
    "max_filesize_mb": 100,
    "low_priority": True,
    "auto_quarantine": False,
    "include_external": False,
    "notify_on_finish": True,
    "exclusions": [],
}

LABELS = {
    "scan_archives": "Scan inside archives",
    "detect_pua": "Detect potentially unwanted apps",
    "max_filesize_mb": "Largest file to scan (MB)",
    "low_priority": "Low-priority scanning",
    "auto_quarantine": "Quarantine threats automatically",
    "include_external": "Include external drives in full scans",
    "notify_on_finish": "Notify when a scan finishes",
    "exclusions": "Excluded folders",
}


class JsonStore:
    def __init__(self, path, defaults):
        self.path = path
        self._defaults = defaults
        self._data = dict(defaults)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self._data.update(loaded)
        except (FileNotFoundError, ValueError):
            pass

    def get(self, key):
        return self._data.get(key, self._defaults.get(key))

    def set(self, key, value):
        self._data[key] = value
        self.save()

    def save(self):
        tmp = str(self.path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)
        os.replace(tmp, self.path)


class Settings(JsonStore):
    def __init__(self, path=None):
        super().__init__(path or (paths.config_dir() / "settings.json"), DEFAULTS)

    def helper_scan_args(self):
        args = [
            "--archives", "yes" if self.get("scan_archives") else "no",
            "--max-filesize", str(int(self.get("max_filesize_mb"))),
        ]
        if self.get("detect_pua"):
            args.append("--pua")
        if self.get("low_priority"):
            args.append("--low-priority")
        if self.get("include_external"):
            args.append("--include-external")
        for folder in self.get("exclusions") or []:
            if os.path.isabs(folder):
                args += ["--exclude", folder]
        return args


class State(JsonStore):
    """Remembers the last scan so the overview can show it."""

    def __init__(self, path=None):
        super().__init__(path or (paths.data_dir() / "state.json"), {"last_scan": None})
