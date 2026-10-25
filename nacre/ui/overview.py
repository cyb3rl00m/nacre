"""Overview: protection status at a glance."""

from gi.repository import Adw, Gdk, Gtk

from .. import clamav
from . import common

INSTALL_CMD = "sudo apt install clamav clamav-freshclam"
KIND_NAMES = {"quick": "Quick scan", "full": "Full system scan", "custom": "Custom scan"}


class OverviewPage(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self._primary_cb = None
        self._secondary_cb = None

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=32)

        # hero
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER)
        hero.set_margin_top(12)
        self.badge, self.badge_icon = common.hero_badge("security-high-symbolic", 72)
        self.badge.set_margin_bottom(10)
        self.title = common.label("Checking protection…", ("title-1",), xalign=0.5, wrap=True,
                                  justify=Gtk.Justification.CENTER)
        self.subtitle = common.label("", ("dim-label", "hero-subtitle"), xalign=0.5, wrap=True,
                                     justify=Gtk.Justification.CENTER)
        self.subtitle.set_max_width_chars(52)
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        buttons.set_margin_top(14)
        self.primary = Gtk.Button()
        self.primary.add_css_class("pill")
        self.primary.add_css_class("suggested-action")
        self.primary.connect("clicked", lambda *_: self._primary_cb and self._primary_cb())
        self.secondary = Gtk.Button()
        self.secondary.add_css_class("pill")
        self.secondary.connect("clicked", lambda *_: self._secondary_cb and self._secondary_cb())
        buttons.append(self.primary)
        buttons.append(self.secondary)
        for w in (self.badge, self.title, self.subtitle, buttons):
            hero.append(w)
        column.append(hero)

        # protection details
        details = Adw.PreferencesGroup(title="Protection")
        self.row_defs = common.row("Virus definitions", "Checking…", "software-update-available-symbolic")
        self.defs_status = common.icon("emblem-ok-symbolic", 16)
        self.row_defs.add_suffix(self.defs_status)
        update_btn = common.button(icon_name="view-refresh-symbolic", css=("flat",),
                                   tooltip="Update virus definitions", on_click=win.show_update)
        update_btn.set_valign(Gtk.Align.CENTER)
        self.row_defs.add_suffix(update_btn)
        self.row_engine = common.row("ClamAV engine", "Checking…", "application-x-executable-symbolic")
        self.row_auto = common.row("Automatic updates", "Checking…", "emblem-synchronizing-symbolic")
        self.row_auto.set_subtitle_lines(2)
        self.row_last = common.row("Last scan", "Never", "document-open-recent-symbolic",
                                   on_activate=self._open_last_scan)
        self.row_quar = common.row("Quarantine", "Empty", "changes-prevent-symbolic",
                                   on_activate=lambda: win.navigate("quarantine"))
        for r in (self.row_defs, self.row_engine, self.row_auto, self.row_last, self.row_quar):
            details.add(r)
        column.append(details)

        # shortcuts
        shortcuts = Adw.PreferencesGroup(title="Scan")
        shortcuts.add(common.row("Quick scan", "Downloads, Desktop, temp folders and autostart entries",
                                 "system-search-symbolic", on_activate=lambda: win.start_scan("quick")))
        shortcuts.add(common.row("Full system scan", "Every file on this computer · needs your password",
                                 "drive-harddisk-symbolic", on_activate=lambda: win.start_scan("full")))
        shortcuts.add(common.row("Custom scan", "Choose the files or folders to check",
                                 "folder-open-symbolic", on_activate=lambda: win.navigate("scan")))
        column.append(shortcuts)

        self.append(common.scrolled_clamp(column, 720, 36))

        for event in ("engine", "quarantine", "scan-started", "scan-finished", "threats-changed",
                      "update-running"):
            win.bus.listen(event, lambda *_: self.refresh())
        win.bus.listen("scan-progress", self._on_progress)
        self.refresh()

    def on_shown(self):
        self.refresh()

    # ----------------------------------------------------------------- state
    def _set_hero(self, state, icon_name, title, subtitle, primary=None, secondary=None):
        common.set_state_class(self.badge, state)
        self.badge_icon.set_from_icon_name(icon_name)
        self.title.set_text(title)
        self.subtitle.set_text(subtitle)
        for btn, spec, attr in ((self.primary, primary, "_primary_cb"), (self.secondary, secondary, "_secondary_cb")):
            if spec:
                btn.set_label(spec[0])
                setattr(self, attr, spec[1])
                btn.set_visible(True)
            else:
                setattr(self, attr, None)
                btn.set_visible(False)

    def refresh(self):
        win = self.win
        e = win.engine
        job = win.scan_job
        last = win.last_result
        unresolved = win.unresolved_threats()
        quick = ("Quick Scan", lambda: win.start_scan("quick"))
        update = ("Update Definitions", win.show_update)

        if e is None:
            self._set_hero("neutral", "security-medium-symbolic", "Checking protection…",
                           "Looking for the ClamAV engine.")
        elif not e.installed:
            self._set_hero("error", "dialog-error-symbolic", "ClamAV isn't installed",
                           f"Nacre uses the ClamAV engine to find threats. Install it from a terminal:\n{INSTALL_CMD}",
                           ("Copy Command", self._copy_install), ("Check Again", win.refresh_engine))
        elif not e.has_definitions:
            self._set_hero("warning", "software-update-urgent-symbolic", "Virus definitions are missing",
                           "ClamAV needs its signature database before it can scan. It's a one-time download.",
                           ("Download Definitions", win.show_update))
        elif job is not None and job.running:
            self._set_hero("busy", "system-search-symbolic", "Scan in progress",
                           self._progress_text(job), ("View Progress", lambda: win.navigate("scan")))
        elif unresolved:
            n = len(unresolved)
            self._set_hero("error", "security-low-symbolic", "Threats need your attention",
                           f"{n} threat{'s' if n != 1 else ''} from your last scan "
                           f"{'are' if n != 1 else 'is'} still on this computer.",
                           ("Review Threats", self._open_last_scan), quick)
        elif e.definitions_outdated:
            when = clamav.relative_time(e.db_date)
            self._set_hero("warning", "security-medium-symbolic", "Virus definitions are out of date",
                           f"They were last updated {when}. New threats appear every day.",
                           ("Update Now", win.show_update), quick)
        elif not last:
            self._set_hero("neutral", "security-high-symbolic", "Ready to scan",
                           "Run a quick scan to check the places threats usually land.", quick, update)
        else:
            when = clamav.relative_time(clamav.parse_iso(last.get("finished")))
            self._set_hero("ok", "security-high-symbolic", "No threats found",
                           f"Your last {KIND_NAMES.get(last.get('kind'), 'scan').lower()} ({when}) came back clean.",
                           quick, update)

        self._refresh_rows()

    def _refresh_rows(self):
        win = self.win
        e = win.engine
        if e is not None:
            if not e.installed:
                self.row_engine.set_subtitle("Not installed")
                self.row_defs.set_subtitle("-")
                self.defs_status.set_from_icon_name("dialog-error-symbolic")
                common.set_state_class(self.defs_status, "error")
            else:
                self.row_engine.set_subtitle(f"ClamAV {e.engine_version or 'unknown version'}")
                if e.has_definitions:
                    when = clamav.relative_time(e.db_date) if e.db_date else "unknown date"
                    self.row_defs.set_subtitle(f"Version {e.db_version} · updated {when}")
                else:
                    self.row_defs.set_subtitle("Not downloaded yet")
                if e.has_definitions and not e.definitions_outdated:
                    self.defs_status.set_from_icon_name("emblem-ok-symbolic")
                    common.set_state_class(self.defs_status, "ok")
                else:
                    self.defs_status.set_from_icon_name("dialog-warning-symbolic")
                    common.set_state_class(self.defs_status, "warning")
            if e.auto_updates is True:
                self.row_auto.set_subtitle("On - the clamav-freshclam service checks for new definitions in the background")
            elif e.auto_updates is False:
                self.row_auto.set_subtitle("Off - turn on with: sudo systemctl enable --now clamav-freshclam")
            else:
                self.row_auto.set_subtitle("Unknown")

        last = win.last_result
        if last:
            when = clamav.relative_time(clamav.parse_iso(last.get("finished")))
            n = len(last.get("threats", []))
            result = f"{n} threat{'s' if n != 1 else ''} found" if n else "no threats"
            self.row_last.set_subtitle(
                f"{KIND_NAMES.get(last.get('kind'), 'Scan')} · {when} · {last.get('files', 0):,} files · {result}")
        else:
            self.row_last.set_subtitle("Never")

        q = len(win.quarantine)
        self.row_quar.set_subtitle(f"{q} item{'s' if q != 1 else ''} locked away" if q else "Empty")

    # --------------------------------------------------------------- helpers
    def _progress_text(self, job):
        stats = job.stats
        name = KIND_NAMES.get(job.kind, "Scan")
        if not stats.engine_ready:
            return f"{name} · loading virus definitions…"
        threats = len(stats.threats)
        extra = f" · {threats} threat{'s' if threats != 1 else ''} found" if threats else ""
        if job.kind == "full" and self.win.shutdown_after:
            extra += " · shuts down when done"
        return f"{name} · {stats.files_scanned:,} files checked{extra}"

    def _on_progress(self, job):
        if self.win.stack.get_visible_child() is self and job.running:
            self.subtitle.set_text(self._progress_text(job))

    def _open_last_scan(self):
        self.win.pages["scan"].show_result(self.win.last_result)
        self.win.navigate("scan")

    def _copy_install(self):
        Gdk.Display.get_default().get_clipboard().set(INSTALL_CMD)
        self.win.toast("Command copied - paste it into a terminal")
