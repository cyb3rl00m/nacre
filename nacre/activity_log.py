"""The activity log: every scan, result and action Nacre performs.

Entries are stored one JSON object per line. Anything older than
RETENTION_DAYS is pruned automatically (on start-up and every hour while the
app is running), so the log always covers the last 30 days and never grows
without bound.
"""

import datetime
import json
import os
import threading

from . import paths

RETENTION_DAYS = 30

CATEGORIES = {
    "scan": "Scans",
    "threat": "Threats",
    "quarantine": "Quarantine",
    "update": "Updates",
    "settings": "Settings",
    "app": "App",
}

LEVELS = ("info", "success", "warning", "error")


def _now():
    return datetime.datetime.now().astimezone()


class LogEntry:
    __slots__ = ("time", "category", "level", "message", "details")

    def __init__(self, time, category, level, message, details):
        self.time = time
        self.category = category
        self.level = level
        self.message = message
        self.details = details or {}

    @classmethod
    def from_json(cls, data):
        return cls(
            datetime.datetime.fromisoformat(data["time"]),
            data.get("category", "app"),
            data.get("level", "info"),
            data.get("message", ""),
            data.get("details") or {},
        )

    def to_json(self):
        return {
            "time": self.time.isoformat(timespec="seconds"),
            "category": self.category,
            "level": self.level,
            "message": self.message,
            "details": self.details,
        }

    def as_text(self):
        line = f"{self.time:%Y-%m-%d %H:%M:%S}  [{self.level.upper():7}] [{self.category}] {self.message}"
        for key, value in self.details.items():
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            line += f"\n{'':32}{key}: {value}"
        return line


class ActivityLog:
    def __init__(self, path=None, retention_days=RETENTION_DAYS):
        self.path = path or (paths.data_dir() / "activity.jsonl")
        self.retention = datetime.timedelta(days=retention_days)
        self._lock = threading.Lock()
        self._listeners = []

    # -- listeners ---------------------------------------------------------
    def connect(self, callback):
        self._listeners.append(callback)

    def _notify(self, entry):
        for cb in list(self._listeners):
            try:
                cb(entry)
            except Exception:  # a broken listener must never break logging
                pass

    # -- writing -----------------------------------------------------------
    def add(self, category, message, level="info", **details):
        if level not in LEVELS:
            level = "info"
        entry = LogEntry(_now(), category, level, message, details)
        line = json.dumps(entry.to_json(), ensure_ascii=False) + "\n"
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line)
        self._notify(entry)
        return entry

    # -- reading -----------------------------------------------------------
    def _read_all(self, bad=None):
        entries = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entries.append(LogEntry.from_json(json.loads(raw)))
                    except (ValueError, KeyError, TypeError):
                        if bad is not None:
                            bad.append(raw)  # corrupted line - dropped on the next prune
                        continue
        except FileNotFoundError:
            pass
        return entries

    def entries(self):
        """All entries from the retention window, newest first."""
        cutoff = _now() - self.retention
        with self._lock:
            items = [e for e in self._read_all() if e.time >= cutoff]
        items.sort(key=lambda e: e.time, reverse=True)
        return items

    # -- maintenance -------------------------------------------------------
    def prune(self):
        """Drop entries older than the retention window. Returns how many were removed."""
        cutoff = _now() - self.retention
        with self._lock:
            bad = []
            entries = self._read_all(bad)
            keep = [e for e in entries if e.time >= cutoff]
            removed = len(entries) - len(keep) + len(bad)
            if removed:
                self._rewrite(keep)
        return removed

    def clear(self):
        with self._lock:
            self._rewrite([])

    def _rewrite(self, entries):
        tmp = str(self.path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for e in sorted(entries, key=lambda e: e.time):
                fh.write(json.dumps(e.to_json(), ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)

    def export_text(self, target, entries=None):
        entries = entries if entries is not None else self.entries()
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(f"Nacre activity log - exported {_now():%Y-%m-%d %H:%M}\n")
            fh.write(f"Entries: {len(entries)} (last {self.retention.days} days)\n\n")
            for e in sorted(entries, key=lambda e: e.time):
                fh.write(e.as_text() + "\n")
