"""Running the helper in the background without freezing the window.

Everything here is GTK-free: results are handed back through a `schedule`
callable (the GUI passes GLib.idle_add) so the code is easy to test.
"""

import json
import os
import subprocess
import sys
import threading
import time

from . import paths
from .clamav import ScanStats

# pkexec exit codes
AUTH_DISMISSED = 126
AUTH_DENIED = 127
CANCELLED = 130


def _direct(fn, *args):
    fn(*args)


def helper_command(args, elevated):
    helper = paths.helper_path()
    if paths.is_installed():
        base = [helper]
    else:
        # Running from the source tree: call it through python so it works
        # even if the executable bit got lost (e.g. downloaded as a zip).
        base = ["/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable, "-I", helper]
    if elevated and os.geteuid() != 0:
        base = ["pkexec"] + base
    return base + list(args)


def auth_failed(code):
    return code in (AUTH_DISMISSED, AUTH_DENIED)


class HelperResult:
    def __init__(self, code, items, output):
        self.code = code
        self.items = items      # parsed JSON lines
        self.output = output    # everything else the helper printed

    @property
    def auth_failed(self):
        return auth_failed(self.code)

    @property
    def succeeded(self):
        return [i for i in self.items if i.get("ok")]

    @property
    def failed(self):
        return [i for i in self.items if not i.get("ok")]


def run_json(args, payload=None, elevated=True, on_done=None, schedule=_direct):
    """Run a helper file command (quarantine/restore/delete…) in a thread."""

    def work():
        cmd = helper_command(args, elevated)
        try:
            proc = subprocess.run(
                cmd,
                input=json.dumps(payload) if payload is not None else "",
                capture_output=True,
                text=True,
                errors="surrogateescape",
            )
            code, out, err = proc.returncode, proc.stdout, proc.stderr
        except OSError as exc:
            code, out, err = 1, "", str(exc)
        items, other = [], []
        for line in out.splitlines():
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    items.append(obj)
                    continue
            except ValueError:
                pass
            other.append(line)
        result = HelperResult(code, items, "\n".join(other + [err]).strip())
        if on_done:
            schedule(on_done, result)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


class StreamJob:
    """Run a helper command and hand every output line to on_line (used for updates)."""

    def __init__(self, args, elevated, on_line, on_done, schedule=_direct):
        self.args = args
        self.elevated = elevated
        self.on_line = on_line
        self.on_done = on_done
        self.schedule = schedule
        self.output = []

    def start(self):
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        cmd = helper_command(self.args, self.elevated)
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1,
            )
        except OSError as exc:
            self.schedule(self.on_done, 1, str(exc))
            return
        for line in proc.stdout:
            line = line.rstrip("\n")
            self.output.append(line)
            self.schedule(self.on_line, line)
        code = proc.wait()
        self.schedule(self.on_done, code, "\n".join(self.output))


class ScanJob:
    """A running clamscan (through the helper). Stats are updated from a reader thread.

    The GUI polls `stats` on a timer instead of getting a callback per file,
    which keeps it smooth even when clamscan prints thousands of lines a second.
    """

    def __init__(self, kind, targets, settings_args, elevated, count_excludes=(), quarantine_found=False):
        self.kind = kind                    # "quick" | "full" | "custom"
        self.targets = list(targets)
        self.elevated = elevated
        self.settings_args = list(settings_args)
        self.count_excludes = list(count_excludes)
        # elevated scans can quarantine detections themselves (no extra password prompt)
        self.quarantine_found = quarantine_found and elevated
        self.stats = ScanStats()
        self.total_files = None             # filled in by the counting thread
        self.returncode = None
        self.cancel_requested = False
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._proc = None
        self.done = threading.Event()

    def command(self):
        args = ["scan", "--watch-stdin"] + self.settings_args
        if self.quarantine_found:
            args.append("--quarantine-found")
        if self.kind == "full":
            args.append("--full")
        else:
            args += self.targets
        return helper_command(args, self.elevated)

    def start(self):
        from .clamav import count_files

        def count():
            total = count_files(self.targets, self.count_excludes, self._stop)
            with self.lock:
                self.total_files = total

        threading.Thread(target=count, daemon=True).start()

        self.stats.started = time.monotonic()
        try:
            self._proc = subprocess.Popen(
                self.command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="surrogateescape",
                bufsize=1,
            )
        except OSError as exc:
            with self.lock:
                self.stats.errors.append((None, str(exc)))
                self.returncode = 2
                self.stats.finished = time.monotonic()
            self.done.set()
            return
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self._proc.stdout:
            with self.lock:
                self.stats.feed(line)
        code = self._proc.wait()
        self._proc.stdout.close()
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except OSError:
            pass
        self._stop.set()
        with self.lock:
            self.returncode = code
            self.stats.finished = time.monotonic()
        self.done.set()

    def cancel(self):
        self.cancel_requested = True
        self._stop.set()
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()          # the helper watches stdin and stops clamscan
        except OSError:
            pass
        try:
            proc.terminate()                # works when the helper isn't running as root
        except (PermissionError, ProcessLookupError, OSError):
            pass

    @property
    def running(self):
        return not self.done.is_set()

    def outcome(self):
        """'clean' | 'threats' | 'cancelled' | 'auth' | 'failed'"""
        code = self.returncode
        if self.cancel_requested or code == CANCELLED:
            return "cancelled"
        if auth_failed(code) and not self.stats.engine_ready:
            return "auth"
        if self.stats.threats:
            return "threats"
        if code in (0, 1):
            return "clean"
        # 2 = errors happened, but if clamscan got through the files we still
        # have a (clean) result - e.g. a few "Access denied" files.
        if self.stats.summary or self.stats.files_scanned:
            return "clean"
        return "failed"

    def failure_reason(self):
        for path, msg in reversed(self.stats.errors):
            if path is None and msg:
                return msg
        if self.stats.output_tail:
            return self.stats.output_tail[-1]
        return f"clamscan exited with code {self.returncode}"
