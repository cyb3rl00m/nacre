"""The main window and the app's central logic (scans, quarantine, updates, logging)."""

import datetime
import os
import subprocess
import threading
import time

from gi.repository import Adw, Gio, GLib, Gtk

from . import APP_ID, APP_NAME
from . import clamav
from .activity_log import ActivityLog
from .runner import ScanJob, StreamJob, run_json
from .settings import Settings, State
from .ui import common
from .ui.logs import LogsPage
from .ui.overview import OverviewPage
from .ui.preferences import build_preferences
from .ui.quarantine import QuarantinePage
from .ui.scan import ScanPage
from .ui.update_dialog import UpdateDialog

PAGES = [
    ("overview", "Overview", "security-high-symbolic"),
    ("scan", "Scan", "system-search-symbolic"),
    ("quarantine", "Quarantine", "changes-prevent-symbolic"),
    ("logs", "Activity Log", "document-open-recent-symbolic"),
]

KIND_NAMES = {"quick": "Quick scan", "full": "Full system scan", "custom": "Custom scan"}
MAX_STORED_THREATS = 500
SHUTDOWN_COUNTDOWN = 60  # seconds to cancel an automatic shutdown


class Bus:
    """A tiny publish/subscribe helper so pages can react to app-wide changes."""

    def __init__(self):
        self._subs = {}

    def listen(self, name, callback):
        self._subs.setdefault(name, []).append(callback)

    def send(self, name, *args):
        for cb in list(self._subs.get(name, [])):
            cb(*args)


def idle(fn, *args):
    """Run fn(*args) on the GTK main loop (used by the background threads)."""
    def call():
        fn(*args)
        return GLib.SOURCE_REMOVE
    GLib.idle_add(call)


class NacreWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title(APP_NAME)
        self.set_default_size(1040, 720)
        self.set_size_request(360, 520)

        self.bus = Bus()
        self.settings = Settings()
        self.state = State()
        self.log = ActivityLog()
        self.engine = None
        self.quarantine = []
        self.scan_job = None
        self.update_job = None
        self.update_dialog = None
        self.shutdown_after = False      # "shut down when the full scan is done" tick box
        self._shutdown_timer = 0
        self.last_result = self.state.get("last_scan")
        self._closing = False

        self.log.prune()
        GLib.timeout_add_seconds(3600, self._prune_tick)

        self._build()
        self._add_actions()
        self.connect("close-request", self._on_close_request)

        self.refresh_engine()
        self.refresh_quarantine()

    # ----------------------------------------------------------------- layout
    def _build(self):
        self.split = Adw.NavigationSplitView()
        self.split.set_min_sidebar_width(210)
        self.split.set_max_sidebar_width(270)

        # sidebar
        sidebar_header = Adw.HeaderBar()
        menu = Gio.Menu()
        section = Gio.Menu()
        section.append("Preferences", "app.preferences")
        section.append("About Nacre", "app.about")
        menu.append_section(None, section)
        quit_section = Gio.Menu()
        quit_section.append("Quit", "app.quit")
        menu.append_section(None, quit_section)
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        menu_btn.set_primary(True)
        menu_btn.set_tooltip_text("Main Menu")
        sidebar_header.pack_end(menu_btn)

        self.nav = Gtk.ListBox()
        self.nav.add_css_class("navigation-sidebar")
        self.nav.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.nav_rows = {}
        self.nav_extras = {}
        for key, title, icon_name in PAGES:
            r = Gtk.ListBoxRow()
            r.page_key = key
            box = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=6, margin_end=6)
            box.append(common.icon(icon_name))
            box.append(self._nav_label(title))
            extra = Gtk.Stack()
            extra.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
            extra.set_hhomogeneous(False)
            spinner = Gtk.Spinner()
            count = common.label("", ("dim-label", "numeric", "nav-count"))
            extra.add_named(Gtk.Box(), "none")
            extra.add_named(spinner, "spinner")
            extra.add_named(count, "count")
            box.append(extra)
            self.nav_extras[key] = (extra, spinner, count)
            r.set_child(box)
            self.nav.append(r)
            self.nav_rows[key] = r
        self.nav.connect("row-selected", self._on_nav_selected)

        sidebar_view = Adw.ToolbarView()
        sidebar_view.add_top_bar(sidebar_header)
        sidebar_view.set_content(Gtk.ScrolledWindow(child=self.nav, hscrollbar_policy=Gtk.PolicyType.NEVER))
        sidebar_page = Adw.NavigationPage.new(sidebar_view, APP_NAME)
        self.split.set_sidebar(sidebar_page)

        # content
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(150)
        self.pages = {
            "overview": OverviewPage(self),
            "scan": ScanPage(self),
            "quarantine": QuarantinePage(self),
            "logs": LogsPage(self),
        }
        for key, page in self.pages.items():
            self.stack.add_named(page, key)

        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(self.stack)

        self.banner = Adw.Banner()
        self.banner.connect("button-clicked", lambda *_: self._on_banner_clicked())

        content_view = Adw.ToolbarView()
        content_view.add_top_bar(Adw.HeaderBar())
        content_view.add_top_bar(self.banner)
        content_view.set_content(self.toasts)
        self.content_page = Adw.NavigationPage.new(content_view, "Overview")
        self.split.set_content(self.content_page)

        self.set_content(self.split)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        self.nav.select_row(self.nav_rows["overview"])

    @staticmethod
    def _nav_label(title):
        lbl = common.label(title)
        lbl.set_hexpand(True)
        return lbl

    def _add_actions(self):
        quick = Gio.SimpleAction.new("quick-scan", None)
        quick.connect("activate", lambda *_: self.start_scan("quick"))
        self.add_action(quick)

        update = Gio.SimpleAction.new("update", None)
        update.connect("activate", lambda *_: self.show_update())
        self.add_action(update)

        page = Gio.SimpleAction.new("page", GLib.VariantType.new("s"))
        page.connect("activate", lambda _a, param: self.navigate(param.get_string()))
        self.add_action(page)

    # ------------------------------------------------------------- navigation
    def _on_nav_selected(self, _box, row):
        if row is None:
            return
        key = row.page_key
        self.stack.set_visible_child_name(key)
        self.content_page.set_title(dict((k, t) for k, t, _ in PAGES)[key])
        self.split.set_show_content(True)
        self.pages[key].on_shown()
        self._update_banner()

    def navigate(self, key):
        if key in self.nav_rows:
            self.nav.select_row(self.nav_rows[key])

    def toast(self, text, timeout=4):
        t = Adw.Toast.new(text)
        t.set_timeout(timeout)
        self.toasts.add_toast(t)

    # ----------------------------------------------------------------- engine
    def refresh_engine(self):
        def work():
            info = clamav.engine_info()
            idle(self._set_engine, info)
        threading.Thread(target=work, daemon=True).start()

    def _set_engine(self, info):
        self.engine = info
        self._update_banner()
        self.bus.send("engine")

    def _update_banner(self):
        e = self.engine
        page = self.stack.get_visible_child_name()
        show = False
        if e is not None and page != "overview":
            if not e.installed:
                self.banner.set_title("ClamAV isn't installed - Nacre can't scan yet.")
                self.banner.set_button_label("How to Install")
                show = True
            elif not e.has_definitions:
                self.banner.set_title("Virus definitions are missing.")
                self.banner.set_button_label("Download")
                show = True
        self.banner.set_revealed(show)

    def _on_banner_clicked(self):
        if self.engine and not self.engine.installed:
            self.navigate("overview")
        else:
            self.show_update()

    def can_elevate(self):
        if os.geteuid() == 0 or (self.engine and self.engine.pkexec):
            return True
        common.alert(
            self, "pkexec is missing",
            "Nacre uses pkexec to ask for your password before changing protected files.\n\n"
            "Install it with:  sudo apt install pkexec",
            [("ok", "OK")],
        )
        return False

    def require_engine(self):
        e = self.engine
        if e is None:
            self.toast("Still checking ClamAV - try again in a moment")
            return False
        if not e.installed:
            common.alert(
                self, "ClamAV isn't installed",
                "Install the ClamAV engine first:\n\nsudo apt install clamav clamav-freshclam",
                [("ok", "OK")],
            )
            return False
        if not e.has_definitions:
            common.alert(
                self, "Virus definitions are missing",
                "ClamAV needs its signature database before it can scan. Download it now?",
                [("cancel", "Cancel"), ("update", "Download")], suggested="update", default="update",
                callback=lambda r: r == "update" and self.show_update(),
            )
            return False
        return True

    # ------------------------------------------------------------------- scan
    def quick_targets(self):
        return clamav.quick_scan_targets(
            GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD),
            GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP),
        )

    def start_scan(self, kind, targets=None, elevated=False):
        if self.scan_job and self.scan_job.running:
            self.navigate("scan")
            self.toast("A scan is already running")
            return
        if not self.require_engine():
            return

        exclusions = list(self.settings.get("exclusions") or [])
        count_excludes = exclusions + [str(clamav.paths.STATE_DIR)]
        if kind == "quick":
            targets = self.quick_targets()
            elevated = False
        elif kind == "full":
            targets = ["/"]
            elevated = True
            count_excludes += clamav.full_scan_excludes(self.settings.get("include_external"))
        targets = clamav.drop_nested([t for t in (targets or []) if t])
        if not targets:
            self.toast("Nothing to scan")
            return
        if elevated and not self.can_elevate():
            return

        job = ScanJob(kind, targets, self.settings.helper_scan_args(), elevated, count_excludes,
                      quarantine_found=bool(self.settings.get("auto_quarantine")))
        self.scan_job = job
        job.start()
        self.log.add(
            "scan", f"{KIND_NAMES[kind]} started", "info",
            locations=targets if kind != "full" else ["/ (entire system)"],
            administrator="yes" if elevated else "no",
            shut_down_when_done="yes" if (kind == "full" and self.shutdown_after) else "no",
        )
        extra, spinner, _ = self.nav_extras["scan"]
        spinner.start()
        extra.set_visible_child_name("spinner")
        self.navigate("scan")
        self.bus.send("scan-started", job)
        GLib.timeout_add(200, self._scan_tick)

    def request_custom_scan(self, targets):
        """Called when files are opened with Nacre (e.g. from the file manager)."""
        if self.engine is None:
            # engine info is still loading; try again shortly
            def retry():
                self.request_custom_scan(targets)
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(400, retry)
            return
        self.start_scan("custom", targets, elevated=False)

    def cancel_scan(self):
        if self.scan_job and self.scan_job.running:
            self.scan_job.cancel()

    def _scan_tick(self):
        job = self.scan_job
        if job is None:
            return GLib.SOURCE_REMOVE
        self.bus.send("scan-progress", job)
        if job.done.is_set():
            self._scan_finished(job)
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _scan_finished(self, job):
        extra, spinner, _ = self.nav_extras["scan"]
        spinner.stop()
        extra.set_visible_child_name("none")

        outcome = job.outcome()
        stats = job.stats
        name = KIND_NAMES[job.kind]
        files = stats.files_scanned
        duration = clamav.human_duration(stats.elapsed)
        errors = len([e for e in stats.errors if e[0]])

        result = {
            "kind": job.kind,
            "outcome": outcome,
            "finished": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "files": files,
            "duration": stats.elapsed,
            "errors": errors,
            "error_samples": [[p, m] for p, m in stats.errors if p][:200],
            "targets": job.targets,
            "threats": [{"path": p, "signature": s, "status": "pending"}
                        for p, s in dict(stats.threats).items()][:MAX_STORED_THREATS],
            "data_scanned": stats.summary.get("Data scanned"),
            "reason": job.failure_reason() if outcome == "failed" else None,
        }

        if outcome == "auth":
            self.log.add("scan", f"{name} cancelled - administrator authentication was dismissed", "warning")
            self.toast("Scan cancelled - authentication was dismissed")
            self.scan_job = None
            self._after_full_scan(job, outcome)
            self.bus.send("scan-finished", None)
            return
        if outcome == "failed":
            self.log.add("scan", f"{name} failed", "error", reason=result["reason"])
        elif outcome == "cancelled":
            self.log.add("scan", f"{name} stopped by you", "warning",
                         files_checked=files, duration=duration, threats_found=len(stats.threats))
        elif outcome == "threats":
            self.log.add("scan", f"{name} finished - {len(stats.threats)} threat{'s' if len(stats.threats) != 1 else ''} found",
                         "error", files_checked=files, duration=duration, unreadable_files=errors)
        else:
            self.log.add("scan", f"{name} finished - no threats found", "success",
                         files_checked=files, duration=duration, unreadable_files=errors)
        for path, sig in stats.threats:
            self.log.add("threat", f"Threat detected: {sig}", "warning", file=clamav.display_text(path),
                         found_by=name)

        # threats the helper already quarantined at the end of an elevated scan
        sigs = dict(stats.threats)
        for item in stats.auto_quarantined:
            path = item.get("path", "")
            for t in result["threats"]:
                if t["path"] == path:
                    t["status"] = "quarantined" if item.get("ok") else "failed"
                    t["error"] = item.get("error")
            if item.get("ok"):
                self.log.add("quarantine", "Moved to quarantine automatically", "success",
                             file=clamav.display_text(path), threat=sigs.get(path, ""))
            else:
                self.log.add("quarantine", "Could not quarantine file", "error",
                             file=clamav.display_text(path), reason=item.get("error", "unknown error"))
        if stats.auto_quarantined:
            self.refresh_quarantine()

        # keep the result unless the scan was stopped before it found anything useful
        if outcome in ("clean", "threats") or (outcome == "cancelled" and stats.threats):
            self.last_result = result
            self.state.set("last_scan", result)
        self.scan_job = None
        self.bus.send("scan-finished", result)
        shutting_down = self._after_full_scan(job, outcome)
        if not shutting_down:
            self._notify_finished(result)

        if (outcome in ("threats", "cancelled") and stats.threats and self.settings.get("auto_quarantine")
                and not job.quarantine_found):
            self.quarantine_threats([t for t in result["threats"]], automatic=True)

    # --------------------------------------------------------------- shutdown
    def set_shutdown_after(self, value):
        value = bool(value)
        if value == self.shutdown_after:
            return
        self.shutdown_after = value
        job = self.scan_job
        running = job is not None and job.running and job.kind == "full"
        target = "the running full scan" if running else "the next full scan"
        if value:
            self.log.add("scan", f"Automatic shutdown turned on for {target}", "info")
        else:
            self.log.add("scan", "Automatic shutdown turned off", "info")
        self.bus.send("shutdown-changed", value)

    def _after_full_scan(self, job, outcome):
        """Handle the shutdown tick box once a full scan ends. Returns True if a shutdown is coming."""
        if job.kind != "full" or not self.shutdown_after:
            return False
        self.shutdown_after = False          # one-off: never carries over to the next scan
        self.bus.send("shutdown-changed", False)
        if outcome not in ("clean", "threats"):
            self.log.add("scan", "Automatic shutdown skipped - the full scan didn't finish", "warning")
            return False
        self._start_shutdown_countdown()
        return True

    def _start_shutdown_countdown(self):
        remaining = [SHUTDOWN_COUNTDOWN]
        state = {"open": True}
        self.log.add("scan", f"Full scan done - shutting down in {SHUTDOWN_COUNTDOWN} seconds", "warning")

        def body():
            return (f"The full scan is finished. This computer will shut down in {remaining[0]} seconds.\n\n"
                    "The scan results are saved - you'll see them next time you open Nacre.")

        def stop_timer():
            if self._shutdown_timer:
                GLib.source_remove(self._shutdown_timer)
                self._shutdown_timer = 0

        def answer(resp):
            if not state["open"]:
                return
            state["open"] = False
            stop_timer()
            if resp == "now":
                self._shutdown_now()
            else:
                self.log.add("scan", "Automatic shutdown cancelled by you", "info")
                self.toast("Shutdown cancelled")

        dlg = common.alert(self, "Shutting down soon", body(),
                           [("cancel", "Cancel Shutdown"), ("now", "Shut Down Now")],
                           destructive="now", default="cancel", callback=answer)

        def tick():
            if not state["open"]:
                return GLib.SOURCE_REMOVE
            remaining[0] -= 1
            if remaining[0] <= 0:
                state["open"] = False
                self._shutdown_timer = 0
                dlg.force_close()
                self._shutdown_now()
                return GLib.SOURCE_REMOVE
            dlg.set_body(body())
            return GLib.SOURCE_CONTINUE

        self._shutdown_timer = GLib.timeout_add_seconds(1, tick)
        self.present()
        notif = Gio.Notification.new(f"Shutting down in {SHUTDOWN_COUNTDOWN} seconds")
        notif.set_body("The full scan is finished. Open Nacre to cancel the shutdown.")
        notif.set_icon(Gio.ThemedIcon.new(APP_ID))
        notif.set_priority(Gio.NotificationPriority.URGENT)
        self.get_application().send_notification("shutdown", notif)

    def _shutdown_now(self):
        self.log.add("scan", "Shutting down the computer after the full scan", "warning")

        def work():
            try:
                res = subprocess.run(["systemctl", "poweroff"], capture_output=True, text=True, timeout=120)
                code, err = res.returncode, (res.stderr or res.stdout).strip()
            except (OSError, subprocess.SubprocessError) as exc:
                code, err = 1, str(exc)
            if code != 0:
                idle(self._shutdown_failed, err or f"systemctl exited with code {code}")

        threading.Thread(target=work, daemon=True).start()

    def _shutdown_failed(self, reason):
        self.log.add("scan", "Automatic shutdown failed", "error", reason=reason)
        common.alert(self, "Couldn't shut down",
                     f"The system refused to shut down:\n{reason}\n\nYou can shut down from the system menu instead.",
                     [("ok", "OK")])

    def _notify_finished(self, result):
        if not self.settings.get("notify_on_finish") or self.is_active():
            return
        n = len(result["threats"])
        name = KIND_NAMES[result["kind"]]
        if result["outcome"] == "failed":
            title, body = f"{name} failed", result.get("reason") or ""
        elif n:
            title, body = f"{n} threat{'s' if n != 1 else ''} found", f"{name} finished. Open Nacre to review."
        else:
            title, body = "No threats found", f"{name} finished - {result['files']:,} files checked."
        notif = Gio.Notification.new(title)
        notif.set_body(body)
        notif.set_icon(Gio.ThemedIcon.new(APP_ID))
        self.get_application().send_notification("scan-finished", notif)

    # ---------------------------------------------------------------- threats
    def unresolved_threats(self):
        r = self.last_result
        if not r:
            return []
        return [t for t in r.get("threats", []) if t.get("status") in ("pending", "failed")]

    def _save_result(self):
        if self.last_result is not None:
            self.state.set("last_scan", self.last_result)
        self.bus.send("threats-changed")

    def _mark(self, path, status, error=None):
        for t in (self.last_result or {}).get("threats", []):
            if t["path"] == path:
                t["status"] = status
                t["error"] = error

    def quarantine_threats(self, threats, automatic=False):
        threats = [t for t in threats if t.get("status") in ("pending", "failed")]
        if not threats or not self.can_elevate():
            return
        payload = [{"path": t["path"], "signature": t["signature"]} for t in threats]
        sigs = {t["path"]: t["signature"] for t in threats}
        self.bus.send("busy", True)

        def done(res):
            self.bus.send("busy", False)
            if res.auth_failed:
                self.log.add("quarantine", "Quarantine cancelled - authentication was dismissed", "warning",
                             files=[clamav.display_text(t["path"]) for t in threats])
                self.toast("Authentication cancelled - nothing was changed")
                return
            if not res.items:
                self.log.add("quarantine", "Quarantine failed", "error", reason=res.output or f"code {res.code}")
                self.toast("Quarantine failed - see the Activity Log")
                return
            for item in res.items:
                path = item.get("path", "")
                if item.get("ok"):
                    self._mark(path, "quarantined")
                    self.log.add("quarantine", "Moved to quarantine" + (" automatically" if automatic else ""),
                                 "success", file=clamav.display_text(path), threat=sigs.get(path, ""))
                else:
                    err = item.get("error", "unknown error")
                    status = "gone" if err == "file no longer exists" else "failed"
                    self._mark(path, status, err)
                    self.log.add("quarantine", "Could not quarantine file", "error",
                                 file=clamav.display_text(path), reason=err)
            ok = len(res.succeeded)
            if ok:
                self.toast(f"{ok} threat{'s' if ok != 1 else ''} moved to quarantine")
            if res.failed:
                self.toast(f"{len(res.failed)} file{'s' if len(res.failed) != 1 else ''} couldn't be quarantined")
            self._save_result()
            self.refresh_quarantine()

        run_json(["quarantine"], payload, elevated=True, on_done=done, schedule=idle)

    def delete_threats(self, threats):
        threats = [t for t in threats if t.get("status") in ("pending", "failed")]
        if not threats or not self.can_elevate():
            return
        sigs = {t["path"]: t["signature"] for t in threats}
        self.bus.send("busy", True)

        def done(res):
            self.bus.send("busy", False)
            if res.auth_failed:
                self.toast("Authentication cancelled - nothing was deleted")
                self.log.add("threat", "Delete cancelled - authentication was dismissed", "warning")
                return
            for item in res.items:
                path = item.get("path", "")
                if item.get("ok"):
                    self._mark(path, "deleted")
                    self.log.add("threat", "Threat deleted permanently", "success",
                                 file=clamav.display_text(path), threat=sigs.get(path, ""))
                else:
                    err = item.get("error", "unknown error")
                    self._mark(path, "gone" if err == "file no longer exists" else "failed", err)
                    self.log.add("threat", "Could not delete file", "error",
                                 file=clamav.display_text(path), reason=err)
            ok = len(res.succeeded)
            if ok:
                self.toast(f"{ok} file{'s' if ok != 1 else ''} deleted")
            self._save_result()

        run_json(["delete-file"], [{"path": t["path"]} for t in threats], elevated=True, on_done=done, schedule=idle)

    def ignore_threat(self, threat):
        self._mark(threat["path"], "ignored")
        self.log.add("threat", "Threat left in place (ignored)", "warning",
                     file=clamav.display_text(threat["path"]), threat=threat["signature"])
        self._save_result()

    # ------------------------------------------------------------- quarantine
    def refresh_quarantine(self):
        self.quarantine = clamav.load_quarantine()
        _, _, count = self.nav_extras["quarantine"]
        extra = self.nav_extras["quarantine"][0]
        if self.quarantine:
            count.set_text(str(len(self.quarantine)))
            extra.set_visible_child_name("count")
        else:
            extra.set_visible_child_name("none")
        self.bus.send("quarantine")

    def restore_items(self, items):
        if not items or not self.can_elevate():
            return
        by_id = {i["id"]: i for i in items}
        self.bus.send("busy", True)

        def done(res):
            self.bus.send("busy", False)
            if res.auth_failed:
                self.toast("Authentication cancelled - nothing was restored")
                return
            for r in res.items:
                item = by_id.get(r.get("id"), {})
                if r.get("ok"):
                    self.log.add("quarantine", "File restored from quarantine", "warning",
                                 file=clamav.display_text(r.get("path", "")), threat=item.get("signature", ""))
                    self.toast("File restored to " + common.short_path(r.get("path", "")))
                else:
                    self.log.add("quarantine", "Could not restore file", "error",
                                 file=clamav.display_text(item.get("original_path", "")), reason=r.get("error"))
                    self.toast("Couldn't restore: " + r.get("error", "unknown error"))
            self.refresh_quarantine()

        run_json(["restore"] + list(by_id), None, elevated=True, on_done=done, schedule=idle)

    def delete_quarantined(self, items=None, everything=False):
        if not self.can_elevate():
            return
        items = list(self.quarantine) if everything else list(items or [])
        if not items:
            return
        by_id = {i["id"]: i for i in items}
        args = ["delete", "--all"] if everything else ["delete"] + list(by_id)
        self.bus.send("busy", True)

        def done(res):
            self.bus.send("busy", False)
            if res.auth_failed:
                self.toast("Authentication cancelled - nothing was deleted")
                return
            ok = 0
            for r in res.items:
                item = by_id.get(r.get("id"), {})
                if r.get("ok"):
                    ok += 1
                    self.log.add("quarantine", "Quarantined file deleted permanently", "success",
                                 file=clamav.display_text(item.get("original_path", r.get("path", ""))),
                                 threat=item.get("signature", ""))
                else:
                    self.log.add("quarantine", "Could not delete quarantined file", "error",
                                 file=clamav.display_text(item.get("original_path", "")), reason=r.get("error"))
            if ok:
                self.toast(f"{ok} file{'s' if ok != 1 else ''} deleted permanently")
            self.refresh_quarantine()

        run_json(args, None, elevated=True, on_done=done, schedule=idle)

    # ---------------------------------------------------------------- update
    def show_update(self):
        if self.engine is not None and not self.engine.freshclam:
            common.alert(
                self, "freshclam isn't installed",
                "Nacre uses ClamAV's freshclam to download virus definitions.\n\n"
                "Install it with:  sudo apt install clamav-freshclam",
                [("ok", "OK")],
            )
            return
        if self.update_job is None and not self.can_elevate():
            return
        if self.update_dialog is None:
            self.update_dialog = UpdateDialog(self)
            self.update_dialog.connect("closed", lambda *_: setattr(self, "update_dialog", None))
        self.update_dialog.present(self)
        if self.update_job is None:
            self._start_update()
        else:
            self.update_dialog.replay(self.update_job.output)

    def _start_update(self):
        old_db = self.engine.db_version if self.engine else None
        started = time.monotonic()
        self.log.add("update", "Virus definition update started", "info")
        self.bus.send("update-running", True)

        def on_line(line):
            if self.update_dialog:
                self.update_dialog.append(line)

        def on_done(code, output):
            self.update_job = None
            self.bus.send("update-running", False)
            status, message, level = interpret_freshclam(code, output)
            if status == "auth":
                self.log.add("update", "Update cancelled - authentication was dismissed", "warning")
            if self.update_dialog:
                self.update_dialog.finish(status, message)
            took = clamav.human_duration(time.monotonic() - started)

            def after_refresh():
                new_db = self.engine.db_version if self.engine else None
                if status == "updated":
                    self.log.add("update", "Virus definitions updated", "success",
                                 previous_version=old_db or "none", new_version=new_db or "unknown", took=took)
                elif status == "current":
                    self.log.add("update", "Virus definitions already up to date", "success",
                                 version=new_db or "unknown")
                elif status == "error":
                    self.log.add("update", "Virus definition update failed", "error", reason=message)
                if status in ("updated", "current"):
                    self.toast(message)

            if status != "auth":
                self._after_engine(after_refresh)
            self.refresh_engine()

        self.update_job = StreamJob(["update"], True, on_line, on_done, schedule=idle)
        self.update_job.start()

    def _after_engine(self, callback):
        def once():
            callback()
            self.bus._subs["engine"].remove(once)
        self.bus.listen("engine", once)

    # ------------------------------------------------------------ preferences
    def show_preferences(self):
        build_preferences(self).present(self)

    # -------------------------------------------------------------- lifecycle
    def _prune_tick(self):
        removed = self.log.prune()
        if removed:
            self.bus.send("log-pruned")
        return GLib.SOURCE_CONTINUE

    def _on_close_request(self, _win):
        if self._closing or not (self.scan_job and self.scan_job.running):
            return False

        def answer(resp):
            if resp == "stop":
                self._closing = True
                self.cancel_scan()
                self.close()

        common.alert(
            self, "Stop the running scan?",
            "Closing Nacre stops the scan that's in progress.",
            [("cancel", "Keep Scanning"), ("stop", "Stop and Quit")],
            destructive="stop", callback=answer,
        )
        return True


def interpret_freshclam(code, output):
    """Turn freshclam's exit code and output into (status, message, level)."""
    from .runner import auth_failed

    text = output or ""
    low = text.lower()
    if auth_failed(code) and "freshclam" not in low and "clamav" not in low:
        return "auth", "Authentication was cancelled. Nothing was changed.", "warning"
    if code == 0:
        if "updated" in low or "downloaded" in low or "database updated" in low:
            return "updated", "Virus definitions updated", "success"
        return "current", "Virus definitions are up to date", "success"
    if code == 1:
        return "current", "Virus definitions are already up to date", "success"
    if "429" in text or "cool-down" in low or "cool down" in low or "rate limit" in low:
        return "error", "The ClamAV mirror is rate-limiting this computer. Please try again in a few hours.", "error"
    if "can't connect" in low or "connection" in low or "resolve" in low or code == 52:
        return "error", "Couldn't reach the ClamAV update servers. Check your internet connection.", "error"
    if "lock" in low:
        return "error", "Another update is already running. Try again in a minute.", "error"
    for line in reversed(text.splitlines()):
        if "error" in line.lower():
            return "error", line.split(":", 1)[-1].strip() or line.strip(), "error"
    return "error", f"freshclam exited with code {code}", "error"
