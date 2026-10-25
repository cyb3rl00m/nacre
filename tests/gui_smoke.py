"""Drive the GUI code end-to-end against fake GTK + fake ClamAV.

Run by test_gui.py in a separate process (it replaces `gi` in sys.modules).
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fake_gi  # noqa: E402

GLib = fake_gi.install()

tmp = Path(tempfile.mkdtemp(prefix=".nacre-smoke-", dir=os.path.expanduser("~")))  # not under /tmp: quick scans check /tmp
home = tmp / "home"
(home / "Downloads").mkdir(parents=True)
(home / "Downloads" / "invoice.pdf.exe").write_text("X5O EICAR-STANDARD-ANTIVIRUS-TEST-FILE")
(home / "Downloads" / "notes.txt").write_text("fine")
bindir = tmp / "bin"
bindir.mkdir()
(bindir / "clamscan").symlink_to(ROOT / "tests" / "fake_clamscan.py")
(bindir / "freshclam").write_text("#!/bin/sh\necho 'Downloading daily-27801.cdiff'\necho 'Database updated'\nexit 0\n")
(bindir / "freshclam").chmod(0o755)
(bindir / "pkexec").write_text("#!/bin/sh\nexec \"$@\"\n")
(bindir / "pkexec").chmod(0o755)
poweroff_marker = tmp / "poweroff-called"
(bindir / "systemctl").write_text(
    f"#!/bin/sh\nif [ \"$1\" = poweroff ]; then touch '{poweroff_marker}'; exit 0; fi\nexit 3\n")
(bindir / "systemctl").chmod(0o755)

os.environ.update({
    "HOME": str(home),
    "XDG_DATA_HOME": str(home / ".local/share"),
    "XDG_CONFIG_HOME": str(home / ".config"),
    "NACRE_STATE_DIR": str(tmp / "state"),
    "NACRE_CLAMSCAN": str(ROOT / "tests" / "fake_clamscan.py"),
    "NACRE_FRESHCLAM": str(bindir / "freshclam"),
    "PATH": f"{bindir}:{os.environ['PATH']}",
})


def wait(cond, what, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


from nacre.main import NacreApp  # noqa: E402
from nacre.window import NacreWindow, interpret_freshclam  # noqa: E402

app = NacreApp()
app.do_startup()
win = NacreWindow(application=app)
wait(lambda: win.engine is not None, "engine info")
assert win.engine.installed and win.engine.db_version == "27800", vars(win.engine)
print("engine:", win.engine.engine_version, win.engine.db_version)

# every page can be shown
for key in ("overview", "scan", "quarantine", "logs"):
    win.navigate(key)
    assert win.stack.get_visible_child_name() == key

# ---- quick scan finds the test file
win.start_scan("quick")
job = win.scan_job
assert job is not None
wait(lambda: job.done.is_set(), "scan to finish")
win._scan_tick()
res = win.last_result
assert res["outcome"] == "threats", res
assert len(res["threats"]) == 1 and res["threats"][0]["path"].endswith("invoice.pdf.exe"), res
assert win.pages["scan"].stack.get_visible_child_name() == "results"
assert win.pages["overview"].title.get_text() == "Threats need your attention", win.pages["overview"].title.get_text()
print("quick scan:", res["files"], "files,", len(res["threats"]), "threat")

# ---- quarantine it
win.quarantine_threats(res["threats"])
wait(lambda: len(win.quarantine) == 1, "quarantine")
assert res["threats"][0]["status"] == "quarantined"
assert not (home / "Downloads" / "invoice.pdf.exe").exists()
assert win.unresolved_threats() == []
win.pages["overview"].refresh()
assert win.pages["overview"].title.get_text() == "No threats found", win.pages["overview"].title.get_text()
win.pages["quarantine"].refresh()
print("quarantined:", win.quarantine[0]["signature"])

# ---- restore, quarantine again, delete all
win.restore_items(list(win.quarantine))
wait(lambda: len(win.quarantine) == 0, "restore")
assert (home / "Downloads" / "invoice.pdf.exe").exists()
win.start_scan("custom", [str(home / "Downloads")])
wait(lambda: win.scan_job.done.is_set(), "custom scan")
win._scan_tick()
win.quarantine_threats(win.last_result["threats"])
wait(lambda: len(win.quarantine) == 1, "second quarantine")
win.delete_quarantined(everything=True)
wait(lambda: len(win.quarantine) == 0, "delete all")
print("restore + delete all: ok")

# ---- delete a threat in place
(home / "Downloads" / "bad2.bin").write_text("EICAR-STANDARD-ANTIVIRUS-TEST-FILE")
win.start_scan("custom", [str(home / "Downloads" / "bad2.bin")])
wait(lambda: win.scan_job.done.is_set(), "scan bad2")
win._scan_tick()
win.delete_threats(win.last_result["threats"])
wait(lambda: not (home / "Downloads" / "bad2.bin").exists(), "delete in place")
time.sleep(0.2)
assert win.last_result["threats"][0]["status"] == "deleted"
print("delete in place: ok")

# ---- clean scan + cancel
(home / "Downloads" / "invoice.pdf.exe").unlink(missing_ok=True)
win.start_scan("quick")
wait(lambda: win.scan_job.done.is_set(), "clean scan")
win._scan_tick()
assert win.last_result["outcome"] == "clean", win.last_result
for i in range(40):
    (home / "Downloads" / f"f{i}.txt").write_text("x")
os.environ["FAKE_CLAMSCAN_DELAY"] = "0.2"
win.start_scan("quick")
job = win.scan_job
wait(lambda: job.stats.files_scanned >= 1, "first file")
win.pages["scan"]._on_progress(job)
win.cancel_scan()
wait(lambda: job.done.is_set(), "cancel", timeout=10)
win._scan_tick()
assert job.outcome() == "cancelled", job.outcome()
os.environ.pop("FAKE_CLAMSCAN_DELAY")
print("cancel: ok")

# ---- update definitions
win.show_update()
wait(lambda: win.update_job is None, "update")
wait(lambda: any(e.category == "update" and "updated" in e.message for e in win.log.entries()), "update log")
assert interpret_freshclam(1, "daily.cld database is up-to-date")[0] == "current"
assert interpret_freshclam(126, "Error executing command as another user: Request dismissed")[0] == "auth"
assert interpret_freshclam(2, "WARNING: ... 429 cool-down")[0] == "error"
print("update: ok")

# ---- preferences + logs
dlg = __import__("nacre.ui.preferences", fromlist=["x"]).build_preferences(win)
win.settings.set("exclusions", ["/home/x/VMs"])
logs = win.pages["logs"]
win.navigate("logs")
logs.refresh()
export = tmp / "export.txt"
win.log.export_text(export)
text = export.read_text()
for needle in ("Quick scan started", "Threat detected", "Moved to quarantine", "File restored",
               "deleted permanently", "Threat deleted permanently", "stopped by you", "Virus definitions updated"):
    assert needle in text, f"missing log entry: {needle}"
print("log entries:", len(win.log.entries()))

# ---- full scan with "shut down when done" (+ auto-quarantine inside the elevated helper run)
import nacre.window as wmod  # noqa: E402
(home / "Downloads" / "late.bin").write_text("EICAR-STANDARD-ANTIVIRUS-TEST-FILE")
win.settings.set("auto_quarantine", True)
os.environ["FAKE_CLAMSCAN_DELAY"] = "0"
scan_page = win.pages["scan"]
scan_page.shutdown_check.set_active(True)
scan_page._on_shutdown_toggled(scan_page.shutdown_check)
assert win.shutdown_after is True
# a quick scan must not trigger the shutdown
(home / "Downloads" / "late.bin").write_text("EICAR-STANDARD-ANTIVIRUS-TEST-FILE")
GLib.timers.clear()
win.start_scan("quick")
wait(lambda: win.scan_job.done.is_set(), "quick scan with shutdown ticked")
win._scan_tick()
assert win.shutdown_after is True and not poweroff_marker.exists()
wait(lambda: len(win.quarantine) >= 1, "post-scan auto quarantine (non-elevated path)")

# full scan: the fake clamscan scans only Downloads (the helper adds "/" which the fake ignores below)
(home / "Downloads" / "late2.bin").write_text("EICAR-STANDARD-ANTIVIRUS-TEST-FILE")
os.environ["FAKE_CLAMSCAN_ONLY"] = str(home / "Downloads")
before = len(win.quarantine)
win.start_scan("full")
job = win.scan_job
assert job.quarantine_found and "--quarantine-found" in job.command()
wait(lambda: job.done.is_set(), "full scan")
GLib.timers.clear()
win._scan_tick()
assert len(win.quarantine) == before + 1, (before, win.quarantine)
assert win.last_result["threats"][0]["status"] == "quarantined", win.last_result["threats"]
assert win.shutdown_after is False                     # one-off
countdown = [fn for fn, _ in GLib.timers]
assert countdown, "no countdown timer started"
tick = countdown[-1]
for _ in range(wmod.SHUTDOWN_COUNTDOWN + 1):
    if tick() is False:
        break
wait(lambda: poweroff_marker.exists(), "systemctl poweroff")
print("full scan + auto-quarantine + shutdown: ok")

# a cancelled full scan never shuts down
poweroff_marker.unlink()
win.set_shutdown_after(True)
os.environ["FAKE_CLAMSCAN_DELAY"] = "0.3"
win.start_scan("full")
job = win.scan_job
wait(lambda: job.stats.files_scanned >= 1, "first file of full scan")
win.cancel_scan()
wait(lambda: job.done.is_set(), "cancel full scan")
GLib.timers.clear()
win._scan_tick()
assert not GLib.timers and not poweroff_marker.exists() and win.shutdown_after is False
os.environ.pop("FAKE_CLAMSCAN_ONLY")
win.settings.set("auto_quarantine", False)
print("cancelled full scan skips shutdown: ok")

# ---- closing while scanning asks first
os.environ["FAKE_CLAMSCAN_DELAY"] = "0.2"
win.start_scan("quick")
assert win._on_close_request(win) is True
win.cancel_scan()
wait(lambda: win.scan_job.done.is_set(), "final cancel")
win._scan_tick()
assert win._on_close_request(win) is False

import shutil
shutil.rmtree(tmp, ignore_errors=True)
print("GUI SMOKE OK")
