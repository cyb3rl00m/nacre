"""Backend tests: run with  python3 -m unittest discover tests  (no GTK needed)."""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "helper" / "nacre-helper"
FAKE = ROOT / "tests" / "fake_clamscan.py"
sys.path.insert(0, str(ROOT))

EICAR_MARKER = "EICAR-STANDARD-ANTIVIRUS-TEST-FILE"


def helper_env(state_dir, **extra):
    env = dict(os.environ)
    env["NACRE_STATE_DIR"] = str(state_dir)
    env["NACRE_CLAMSCAN"] = str(FAKE)
    env.update(extra)
    return env


def run_helper(args, state_dir, stdin="", **extra):
    return subprocess.run(
        [sys.executable, str(HELPER)] + args,
        input=stdin, capture_output=True, text=True, env=helper_env(state_dir, **extra), timeout=60,
    )


class ParserTests(unittest.TestCase):
    def setUp(self):
        from nacre import clamav
        self.c = clamav

    def test_lines(self):
        L = self.c.Line
        p = self.c.parse_line
        self.assertEqual(p("/home/a b/x.exe: OK"), (L.OK, "/home/a b/x.exe", None))
        self.assertEqual(p("/x/e.com: Win.Test.EICAR_HC-1 FOUND"), (L.THREAT, "/x/e.com", "Win.Test.EICAR_HC-1"))
        self.assertEqual(p("/x/we: ird: Eicar FOUND"), (L.THREAT, "/x/we: ird", "Eicar"))
        self.assertEqual(p("/root/x: Access denied. ERROR"), (L.ERROR, "/root/x", "Access denied"))
        self.assertEqual(p("/x/empty: Empty file")[0], L.OK)
        self.assertEqual(p("/proc: Excluded")[0], L.SKIP)
        self.assertEqual(p("LibClamAV Error: cli_loaddbdir: No supported database files found")[0], L.ERROR)
        self.assertEqual(p("WARNING: /x: Can't open file or directory")[0], L.WARNING)
        self.assertEqual(p("----------- SCAN SUMMARY -----------")[0], L.SUMMARY_START)
        self.assertEqual(p("Scanned files: 12", in_summary=True), (L.SUMMARY, "Scanned files", "12"))

    def test_version(self):
        engine, db, date = self.c.parse_version("ClamAV 1.4.3/27800/Sun Oct 25 08:23:01 2026\n")
        self.assertEqual((engine, db), ("1.4.3", "27800"))
        self.assertEqual(date, datetime.datetime(2026, 10, 25, 8, 23, 1))
        self.assertEqual(self.c.parse_version("ClamAV 1.4.3"), ("1.4.3", None, None))
        self.assertEqual(self.c.parse_version(""), (None, None, None))

    def test_stats(self):
        s = self.c.ScanStats()
        for line in ["/a: OK", "/b: Eicar FOUND", "/c: Access denied. ERROR",
                     "----------- SCAN SUMMARY -----------", "Scanned files: 2"]:
            s.feed(line + "\n")
        self.assertEqual(s.files_scanned, 2)
        self.assertEqual(s.threats, [("/b", "Eicar")])
        self.assertEqual(len(s.errors), 1)
        self.assertEqual(s.summary["Scanned files"], "2")

    def test_count_files(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(5):
                Path(d, f"f{i}").write_text("x")
            Path(d, "sub").mkdir()
            Path(d, "sub", "g").write_text("y")
            Path(d, "skip").mkdir()
            Path(d, "skip", "h").write_text("z")
            n = self.c.count_files([d], [os.path.join(d, "skip")], threading.Event())
            self.assertEqual(n, 6)

    def test_formatting(self):
        self.assertEqual(self.c.human_duration(75), "1 min 15 s")
        self.assertEqual(self.c.human_size(2048), "2.0 KB")
        self.assertEqual(self.c.display_text("/a/\udcff.bin"), "/a/�.bin")


class ActivityLogTests(unittest.TestCase):
    def test_add_read_prune(self):
        from nacre.activity_log import ActivityLog, LogEntry
        with tempfile.TemporaryDirectory() as d:
            log = ActivityLog(Path(d, "log.jsonl"))
            log.add("scan", "Quick scan finished", "success", files=10)
            # inject an entry that is 31 days old and one from 29 days ago
            old = LogEntry(datetime.datetime.now().astimezone() - datetime.timedelta(days=31),
                           "app", "info", "ancient", {})
            recent = LogEntry(datetime.datetime.now().astimezone() - datetime.timedelta(days=29),
                              "app", "info", "recent", {})
            with open(log.path, "a") as fh:
                fh.write(json.dumps(old.to_json()) + "\n")
                fh.write(json.dumps(recent.to_json()) + "\n")
                fh.write("this line is corrupt\n")
            msgs = [e.message for e in log.entries()]
            self.assertEqual(msgs, ["Quick scan finished", "recent"])  # old one hidden, newest first
            self.assertEqual(log.prune(), 2)                          # old + corrupt line gone
            self.assertEqual(len(Path(log.path).read_text().splitlines()), 2)
            out = Path(d, "export.txt")
            log.export_text(out)
            self.assertIn("files: 10", out.read_text())
            log.clear()
            self.assertEqual(log.entries(), [])


class SettingsTests(unittest.TestCase):
    def test_helper_args(self):
        from nacre.settings import Settings
        with tempfile.TemporaryDirectory() as d:
            s = Settings(Path(d, "s.json"))
            s.set("detect_pua", True)
            s.set("exclusions", ["/home/me/VMs", "relative/ignored"])
            again = Settings(Path(d, "s.json"))
            args = again.helper_scan_args()
            self.assertIn("--pua", args)
            self.assertIn("/home/me/VMs", args)
            self.assertNotIn("relative/ignored", args)
            self.assertIn("--low-priority", args)


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.state = self.base / "state"
        self.scan_dir = self.base / "files"
        self.scan_dir.mkdir()
        (self.scan_dir / "clean.txt").write_text("hello")
        self.evil = self.scan_dir / "evil file.com"
        self.evil.write_text("X5O!P%@AP " + EICAR_MARKER + " $H+H*")
        os.chmod(self.evil, 0o640)
        (self.scan_dir / "vm").mkdir()
        (self.scan_dir / "vm" / "huge.img").write_text("big")

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_and_args(self):
        argfile = self.base / "args.txt"
        res = run_helper(["scan", "--pua", "--exclude", str(self.scan_dir / "vm"), str(self.scan_dir)],
                         self.state, FAKE_CLAMSCAN_ARGS=str(argfile))
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("evil file.com: Win.Test.EICAR_HC-1 FOUND", res.stdout)
        self.assertIn("clean.txt: OK", res.stdout)
        self.assertNotIn("huge.img", res.stdout)
        args = argfile.read_text().splitlines()
        self.assertIn("--detect-pua=yes", args)
        self.assertIn("--scan-archive=yes", args)
        self.assertTrue(any(a.startswith("--exclude-dir=^") and "state" in a for a in args))

    def test_full_scan_excludes(self):
        argfile = self.base / "args.txt"
        # point the fake at an empty dir so it doesn't walk "/" - we only check args
        res = run_helper(["scan", "--full"], self.state, FAKE_CLAMSCAN_ARGS=str(argfile),
                         FAKE_CLAMSCAN_NOSCAN="1")
        args = argfile.read_text().splitlines()
        self.assertIn("/", args)
        for d in ("/proc", "/sys", "/dev", "/run", "/media", "/mnt"):
            self.assertIn(f"--exclude-dir=^{d}($|/)", args)
        del res

    def test_quarantine_found_during_scan(self):
        res = run_helper(["scan", "--quarantine-found", str(self.scan_dir)], self.state)
        self.assertEqual(res.returncode, 1, res.stdout)
        lines = [l for l in res.stdout.splitlines() if l.startswith("@@NACRE-QUARANTINE ")]
        self.assertEqual(len(lines), 1)
        item = json.loads(lines[0].split(" ", 1)[1])
        self.assertTrue(item["ok"], item)
        self.assertFalse(self.evil.exists())
        index = json.loads((self.state / "quarantine.json").read_text())["items"]
        self.assertEqual(index[0]["signature"], "Win.Test.EICAR_HC-1")
        # the GUI parser picks the result up
        from nacre.clamav import ScanStats
        stats = ScanStats()
        for line in res.stdout.splitlines(True):
            stats.feed(line)
        self.assertEqual(len(stats.threats), 1)
        self.assertTrue(stats.auto_quarantined[0]["ok"])

    def test_rejects_relative_path(self):
        res = run_helper(["scan", "relative/path"], self.state)
        self.assertEqual(res.returncode, 2)

    def test_cancel_via_stdin(self):
        for i in range(50):
            (self.scan_dir / f"f{i}").write_text("x")
        env = helper_env(self.state, FAKE_CLAMSCAN_DELAY="0.2")
        proc = subprocess.Popen([sys.executable, str(HELPER), "scan", "--watch-stdin", str(self.scan_dir)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
        proc.stdout.readline()  # wait for the first file
        start = time.monotonic()
        proc.stdin.close()
        code = proc.wait(timeout=10)
        self.assertEqual(code, 130)
        self.assertLess(time.monotonic() - start, 5)

    def test_quarantine_restore_delete(self):
        original = self.evil.read_bytes()
        payload = json.dumps([{"path": str(self.evil), "signature": "Win.Test.EICAR_HC-1"},
                              {"path": str(self.scan_dir / "missing"), "signature": "X"}])
        res = run_helper(["quarantine"], self.state, stdin=payload)
        lines = [json.loads(l) for l in res.stdout.splitlines()]
        self.assertTrue(lines[0]["ok"])
        self.assertFalse(lines[1]["ok"])
        self.assertEqual(res.returncode, 1)
        self.assertFalse(self.evil.exists())

        index = json.loads((self.state / "quarantine.json").read_text())["items"]
        self.assertEqual(len(index), 1)
        item = index[0]
        qpath = self.state / "quarantine" / (item["id"] + ".nqf")
        stored = qpath.read_bytes()
        self.assertNotIn(EICAR_MARKER.encode(), stored)       # obfuscated on disk
        self.assertEqual(oct(os.stat(self.state / "quarantine").st_mode & 0o777), "0o700")
        self.assertEqual(oct(os.stat(self.state / "quarantine.json").st_mode & 0o777), "0o644")

        # the GUI-side reader sees it
        os.environ["NACRE_STATE_DIR"] = str(self.state)
        import importlib
        from nacre import paths, clamav
        importlib.reload(paths)
        importlib.reload(clamav)
        self.assertEqual(clamav.load_quarantine()[0]["signature"], "Win.Test.EICAR_HC-1")

        # a scan never re-detects quarantined files
        res = run_helper(["scan", str(self.base)], self.state)
        self.assertNotIn(".nqf", res.stdout)

        # restore puts the exact bytes and mode back
        res = run_helper(["restore", item["id"]], self.state)
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertEqual(self.evil.read_bytes(), original)
        self.assertEqual(oct(os.stat(self.evil).st_mode & 0o777), "0o640")
        self.assertFalse(qpath.exists())

        # quarantine again, then delete everything
        run_helper(["quarantine"], self.state, stdin=json.dumps([{"path": str(self.evil), "signature": "S"}]))
        self.assertEqual(len(json.loads((self.state / "quarantine.json").read_text())["items"]), 1)
        res = run_helper(["delete", "--all"], self.state)
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertEqual(json.loads((self.state / "quarantine.json").read_text())["items"], [])
        self.assertEqual(list((self.state / "quarantine").iterdir()), [])

    def test_restore_does_not_overwrite(self):
        run_helper(["quarantine"], self.state, stdin=json.dumps([{"path": str(self.evil), "signature": "S"}]))
        item = json.loads((self.state / "quarantine.json").read_text())["items"][0]
        self.evil.write_text("a new, different file")
        res = run_helper(["restore", item["id"]], self.state)
        out = json.loads(res.stdout.splitlines()[0])
        self.assertTrue(out["ok"])
        self.assertTrue(out["path"].endswith(".restored"))
        self.assertEqual(self.evil.read_text(), "a new, different file")

    def test_bad_ids_rejected(self):
        res = run_helper(["delete", "../../etc/passwd"], self.state)
        self.assertEqual(res.returncode, 1)
        self.assertIn("no such quarantined item", res.stdout)

    def test_delete_file_refuses_dirs_and_symlinks(self):
        link = self.scan_dir / "link"
        link.symlink_to(self.scan_dir / "clean.txt")
        payload = json.dumps([{"path": str(self.scan_dir / "vm")}, {"path": str(link)},
                              {"path": str(self.scan_dir / "clean.txt")}])
        res = run_helper(["delete-file"], self.state, stdin=payload)
        out = [json.loads(l) for l in res.stdout.splitlines()]
        self.assertEqual([o["ok"] for o in out], [False, False, True])
        self.assertTrue((self.scan_dir / "vm").is_dir())
        self.assertFalse((self.scan_dir / "clean.txt").exists())

    def test_update_streams_and_succeeds(self):
        fake_fresh = self.base / "freshclam"
        fake_fresh.write_text("#!/bin/sh\necho 'daily.cld database is up-to-date'\nexit 1\n")
        fake_fresh.chmod(0o755)
        res = run_helper(["update"], self.state, NACRE_FRESHCLAM=str(fake_fresh))
        self.assertIn("up-to-date", res.stdout)
        self.assertEqual(res.returncode, 1)  # freshclam: 1 = already up to date


class ScanJobTests(unittest.TestCase):
    def test_scan_job_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            files = Path(d, "files")
            files.mkdir()
            (files / "a.txt").write_text("fine")
            (files / "bad.bin").write_text(EICAR_MARKER)
            os.environ["NACRE_STATE_DIR"] = str(Path(d, "state"))
            os.environ["NACRE_CLAMSCAN"] = str(FAKE)
            from nacre.runner import ScanJob
            job = ScanJob("custom", [str(files)], ["--archives", "yes"], elevated=False)
            job.start()
            self.assertTrue(job.done.wait(30))
            self.assertEqual(job.outcome(), "threats")
            self.assertEqual(job.stats.files_scanned, 2)
            self.assertEqual(job.total_files, 2)
            self.assertEqual(job.stats.threats[0][1], "Win.Test.EICAR_HC-1")


if __name__ == "__main__":
    unittest.main()
