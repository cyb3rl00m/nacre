"""Talking to ClamAV: engine/definition info, clamscan output parsing, scan targets."""

import datetime
import json
import os
import shutil
import stat
import subprocess
import time

from . import paths

# Same list the helper excludes for a full scan (plus our own state dir).
PSEUDO_DIRS = ("/proc", "/sys", "/dev", "/run")
EXTERNAL_DIRS = ("/media", "/mnt")

OUTDATED_AFTER = datetime.timedelta(days=3)

# Must match RESULT_PREFIX in helper/nacre-helper
QUARANTINE_PREFIX = "@@NACRE-QUARANTINE "


# --------------------------------------------------------------------- engine info

class EngineInfo:
    def __init__(self):
        self.clamscan = shutil.which("clamscan")
        self.freshclam = shutil.which("freshclam")
        self.pkexec = shutil.which("pkexec")
        self.engine_version = None
        self.db_version = None
        self.db_date = None
        self.auto_updates = None  # True/False/None (unknown)

    @property
    def installed(self):
        return self.clamscan is not None

    @property
    def has_definitions(self):
        return self.db_version is not None

    @property
    def definitions_outdated(self):
        if self.db_date is None:
            return True
        return datetime.datetime.now() - self.db_date > OUTDATED_AFTER


def parse_version(text):
    """Parse `clamscan --version`, e.g. 'ClamAV 1.4.3/27800/Sun Oct 25 08:23:01 2026'."""
    text = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    if not text.startswith("ClamAV"):
        return None, None, None
    parts = text[len("ClamAV"):].strip().split("/", 2)
    engine = parts[0].strip() or None
    db_version = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    db_date = None
    if len(parts) > 2:
        raw = " ".join(parts[2].split())
        for fmt in ("%a %b %d %H:%M:%S %Y", "%a %b %d %H:%M:%S %Z %Y"):
            try:
                db_date = datetime.datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    return engine, db_version, db_date


def engine_info():
    info = EngineInfo()
    if info.clamscan:
        env = dict(os.environ, LC_ALL="C", LANG="C")
        try:
            out = subprocess.run(
                [info.clamscan, "--version"], capture_output=True, text=True, timeout=30, env=env
            ).stdout
            info.engine_version, info.db_version, info.db_date = parse_version(out)
        except (OSError, subprocess.SubprocessError):
            pass
    if shutil.which("systemctl"):
        try:
            res = subprocess.run(
                ["systemctl", "is-active", "clamav-freshclam"],
                capture_output=True, text=True, timeout=10,
            )
            info.auto_updates = res.stdout.strip() == "active"
        except (OSError, subprocess.SubprocessError):
            pass
    return info


# ---------------------------------------------------------------- output parsing

class Line:
    OK, THREAT, ERROR, WARNING, SKIP, SUMMARY_START, SUMMARY, OTHER = range(8)


def parse_line(line, in_summary=False):
    """Classify one line of clamscan output. Returns (kind, a, b)."""
    line = line.rstrip("\r\n")
    if not line:
        return Line.OTHER, None, None
    if line.startswith("----------- SCAN SUMMARY"):
        return Line.SUMMARY_START, None, None
    if in_summary:
        key, sep, value = line.partition(":")
        if sep:
            return Line.SUMMARY, key.strip(), value.strip()
        return Line.OTHER, line, None
    if line.endswith(": OK") or line.endswith(": Empty file"):
        return Line.OK, line.rpartition(": ")[0], None
    if line.endswith(" FOUND"):
        path, sep, rest = line[: -len(" FOUND")].rpartition(": ")
        if sep:
            return Line.THREAT, path, rest.strip()
    if line.endswith(": Excluded") or line.endswith(": Symbolic link"):
        return Line.SKIP, line.rpartition(": ")[0], None
    if line.endswith(" ERROR"):
        path, sep, reason = line[: -len(" ERROR")].rpartition(": ")
        if sep:
            return Line.ERROR, path, reason.strip().rstrip(".")
        return Line.ERROR, None, line
    if line.startswith("ERROR:") or line.startswith("LibClamAV Error"):
        return Line.ERROR, None, line.split(":", 1)[1].strip()
    if line.startswith("WARNING:") or line.startswith("LibClamAV Warning"):
        return Line.WARNING, None, line.split(":", 1)[1].strip()
    return Line.OTHER, line, None


class ScanStats:
    def __init__(self):
        self.files_scanned = 0
        self.threats = []        # list of (path, signature)
        self.errors = []         # list of (path or None, message)
        self.warnings = 0
        self.current = ""
        self.engine_ready = False
        self.summary = {}
        self.output_tail = []    # last lines, for diagnosing failures
        self.auto_quarantined = []  # results of the helper's --quarantine-found
        self.started = time.monotonic()
        self.finished = None
        self._in_summary = False

    def feed(self, line):
        if line.startswith(QUARANTINE_PREFIX):
            try:
                self.auto_quarantined.append(json.loads(line[len(QUARANTINE_PREFIX):]))
            except ValueError:
                pass
            return Line.OTHER
        kind, a, b = parse_line(line, self._in_summary)
        if kind in (Line.OK, Line.THREAT, Line.SKIP):
            self.engine_ready = True
            self.current = a
        if kind == Line.OK:
            self.files_scanned += 1
        elif kind == Line.THREAT:
            self.files_scanned += 1
            self.threats.append((a, b))
        elif kind == Line.ERROR:
            self.errors.append((a, b))
        elif kind == Line.WARNING:
            self.warnings += 1
        elif kind == Line.SUMMARY_START:
            self._in_summary = True
        elif kind == Line.SUMMARY:
            self.summary[a] = b
        elif kind == Line.OTHER and a:
            self.output_tail = (self.output_tail + [a])[-20:]
        return kind

    @property
    def elapsed(self):
        end = self.finished if self.finished is not None else time.monotonic()
        return end - self.started


# ------------------------------------------------------------------- scan targets

def quick_scan_targets(download_dir=None, desktop_dir=None):
    """Places where downloaded or dropped malware usually ends up."""
    home = os.path.expanduser("~")
    candidates = [
        download_dir or os.path.join(home, "Downloads"),
        desktop_dir or os.path.join(home, "Desktop"),
        os.path.join(home, ".local", "bin"),
        os.path.join(home, ".local", "share", "applications"),
        os.path.join(home, ".config", "autostart"),
        "/tmp",
        "/var/tmp",
        "/dev/shm",
    ]
    targets = []
    for path in candidates:
        if path and os.path.isdir(path) and path != home:
            targets.append(os.path.realpath(path))
    return drop_nested(targets)


def drop_nested(paths):
    """Remove duplicates and paths that live inside another path in the list."""
    result = []
    for p in sorted(set(paths), key=len):
        if not any(p == r or p.startswith(r.rstrip("/") + "/") for r in result):
            result.append(p)
    # keep the original order
    return [p for p in dict.fromkeys(paths) if p in result]


def count_files(targets, excludes, stop_event, limit=5_000_000, time_limit=120):
    """Count regular files below targets (best effort, for the progress bar).

    Returns the count, or None when stopped / limits were hit.
    """
    excludes = tuple(e.rstrip("/") for e in excludes if e)
    started = time.monotonic()
    total = 0
    stack = []
    for t in targets:
        try:
            st = os.lstat(t)
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode):
            stack.append(t)
        elif stat.S_ISREG(st.st_mode):
            total += 1
    while stack:
        if stop_event.is_set() or total > limit or time.monotonic() - started > time_limit:
            return None
        current = stack.pop()
        if any(current == e or current.startswith(e + "/") for e in excludes):
            continue
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def full_scan_excludes(include_external):
    ex = list(PSEUDO_DIRS) + [str(paths.STATE_DIR)]
    if not include_external:
        ex += list(EXTERNAL_DIRS)
    return ex


# ---------------------------------------------------------------------- quarantine

def load_quarantine():
    """Items in quarantine, newest first. The index is world-readable."""
    try:
        with open(paths.QUARANTINE_INDEX, "r", encoding="utf-8") as fh:
            items = json.load(fh).get("items", [])
    except (FileNotFoundError, PermissionError, ValueError):
        items = []
    items = [i for i in items if isinstance(i, dict) and i.get("id")]
    items.sort(key=lambda i: i.get("quarantined_at", ""), reverse=True)
    return items


# ------------------------------------------------------------------------- display

def display_text(text):
    """Make any path (even with undecodable bytes) safe to show in GTK."""
    if text is None:
        return ""
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def human_size(num):
    num = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def human_duration(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m} min"
    if m:
        return f"{m} min {s} s"
    return f"{s} s"


def relative_time(when):
    if when is None:
        return "never"
    if when.tzinfo is not None:
        now = datetime.datetime.now(when.tzinfo)
    else:
        now = datetime.datetime.now()
    delta = now - when
    secs = delta.total_seconds()
    if secs < 0:
        return "just now"
    if secs < 60:
        return "just now"
    if secs < 3600:
        n = int(secs // 60)
        return f"{n} minute{'s' if n != 1 else ''} ago"
    if secs < 86400:
        n = int(secs // 3600)
        return f"{n} hour{'s' if n != 1 else ''} ago"
    n = delta.days
    if n == 1:
        return "yesterday"
    return f"{n} days ago"


def parse_iso(text):
    if not text:
        return None
    try:
        value = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if value.tzinfo is not None:
        value = value.astimezone()
    return value


__all__ = [
    "EngineInfo", "engine_info", "parse_version", "parse_line", "Line", "ScanStats",
    "quick_scan_targets", "count_files", "full_scan_excludes", "load_quarantine",
    "display_text", "human_size", "human_duration", "relative_time", "parse_iso",
]
