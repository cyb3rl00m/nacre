"""Scan page: choose a scan, watch it run, review the results."""

import os

from gi.repository import Adw, Gdk, GLib, Gtk

from .. import clamav
from . import common

KIND_NAMES = {"quick": "Quick scan", "full": "Full system scan", "custom": "Custom scan"}
KIND_DESCRIPTIONS = {
    "quick": "Checking the places threats usually land",
    "full": "Checking every file on this computer",
    "custom": "Checking the files and folders you picked",
}
STATUS_TEXT = {
    "quarantined": ("Quarantined", "ok"),
    "deleted": ("Deleted", "ok"),
    "ignored": ("Ignored", "warning"),
    "gone": ("File no longer exists", "dim"),
}
MAX_LIVE_ROWS = 100


class ScanPage(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.result = None
        self._live_rows = 0
        self._busy = False

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._build_choose(), "choose")
        self.stack.add_named(self._build_running(), "running")
        self.stack.add_named(self._build_results(), "results")
        self.stack.set_vexpand(True)
        self.append(self.stack)

        win.bus.listen("scan-started", self._on_started)
        win.bus.listen("scan-progress", self._on_progress)
        win.bus.listen("scan-finished", self._on_finished)
        win.bus.listen("threats-changed", lambda: self.result and self._fill_results(self.result))
        win.bus.listen("busy", self._on_busy)
        win.bus.listen("shutdown-changed", self._sync_shutdown)

    def on_shown(self):
        if self.stack.get_visible_child_name() == "choose":
            self._fill_quick_targets()

    # ===================================================================== choose
    def _build_choose(self):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=28)

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head.append(common.label("What would you like to scan?", ("title-2",)))
        head.append(common.label("Nacre checks files against millions of known threats using the ClamAV engine.",
                                 ("dim-label",), wrap=True))
        column.append(head)

        cards = Gtk.FlowBox()
        cards.set_selection_mode(Gtk.SelectionMode.NONE)
        cards.set_homogeneous(True)
        cards.set_min_children_per_line(1)
        cards.set_max_children_per_line(3)
        cards.set_column_spacing(14)
        cards.set_row_spacing(14)
        cards.append(self._card(
            "system-search-symbolic", "Quick Scan",
            "Downloads, Desktop, temp folders and apps that start with your session.",
            "Usually takes a few minutes", lambda: self.win.start_scan("quick"), accent=True))
        cards.append(self._card(
            "drive-harddisk-symbolic", "Full System Scan",
            "Every file on this computer, including system files, to find any and every threat.",
            "Needs your password · can take an hour or more", lambda: self.win.start_scan("full")))
        cards.append(self._card(
            "folder-open-symbolic", "Custom Scan",
            "Pick exactly which folders to check. You can also drop files onto this page.",
            "Choose folders…", self._choose_folders))
        for child in iter_children(cards):
            child.set_focusable(False)
        column.append(cards)

        full = Adw.PreferencesGroup(title="Full system scan")
        self.shutdown_check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        self.shutdown_check.connect("toggled", self._on_shutdown_toggled)
        shut_row = Adw.ActionRow(title="Shut down the computer when the full scan is done",
                                 subtitle="Handy for overnight scans. You get 60 seconds to cancel before it "
                                          "turns off. Only applies to the next full scan.")
        shut_row.add_prefix(self.shutdown_check)
        shut_row.set_activatable_widget(self.shutdown_check)
        full.add(shut_row)
        column.append(full)

        custom = Adw.PreferencesGroup(title="Custom scan")
        custom.add(common.row("Scan folders…", "Check one or more folders and everything inside them",
                              "folder-symbolic", on_activate=self._choose_folders))
        custom.add(common.row("Scan files…", "Check individual files", "text-x-generic-symbolic",
                              on_activate=self._choose_files))
        self.admin_switch = Adw.SwitchRow(title="Use administrator rights",
                                          subtitle="Needed to scan files that belong to other users or the system")
        custom.add(self.admin_switch)
        column.append(custom)

        info = Adw.PreferencesGroup()
        self.quick_expander = Adw.ExpanderRow(title="What does a quick scan check?")
        self.quick_expander.add_prefix(common.icon("dialog-information-symbolic"))
        self._quick_rows = []
        info.add(self.quick_expander)
        column.append(info)

        hint = common.label("Tip: drag files or folders from your file manager and drop them here to scan them.",
                            ("dim-label", "caption"), xalign=0.5, wrap=True)
        column.append(hint)

        page = common.scrolled_clamp(column, 880, 28)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)

        def on_enter(*_):
            page.add_css_class("drop-hover")
            return Gdk.DragAction.COPY

        def on_leave(*_):
            page.remove_css_class("drop-hover")

        drop.connect("enter", on_enter)
        drop.connect("leave", on_leave)
        page.add_controller(drop)
        self._fill_quick_targets()
        return page

    def _card(self, icon_name, title, text, meta, callback, accent=False):
        btn = Gtk.Button()
        btn.add_css_class("card")
        btn.add_css_class("scan-card")
        if accent:
            btn.add_css_class("accent-card")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)
        tile = Gtk.Box(halign=Gtk.Align.START)
        tile.add_css_class("card-icon")
        tile.append(common.icon(icon_name, 28))
        box.append(tile)
        t = common.label(title, ("title-4",))
        t.set_margin_top(6)
        box.append(t)
        d = common.label(text, ("dim-label",), wrap=True)
        d.set_vexpand(True)
        d.set_valign(Gtk.Align.START)
        box.append(d)
        box.append(common.label(meta, ("caption", "card-meta"), wrap=True))
        btn.set_child(box)
        btn.connect("clicked", lambda *_: callback())
        return btn

    def _fill_quick_targets(self):
        for r in self._quick_rows:
            self.quick_expander.remove(r)
        self._quick_rows = []
        for path in self.win.quick_targets():
            r = common.row(common.short_path(path, 4), None, "folder-symbolic")
            self.quick_expander.add_row(r)
            self._quick_rows.append(r)

    def _choose_folders(self):
        dlg = Gtk.FileDialog(title="Choose folders to scan", modal=True)

        def done(d, res):
            try:
                model = d.select_multiple_folders_finish(res)
            except GLib.Error:
                return
            self._start_custom(files_from_model(model))

        dlg.select_multiple_folders(self.win, None, done)

    def _choose_files(self):
        dlg = Gtk.FileDialog(title="Choose files to scan", modal=True)

        def done(d, res):
            try:
                model = d.open_multiple_finish(res)
            except GLib.Error:
                return
            self._start_custom(files_from_model(model))

        dlg.open_multiple(self.win, None, done)

    def _on_drop(self, _target, value, _x, _y):
        self.stack.get_child_by_name("choose").remove_css_class("drop-hover")
        files = value.get_files() if value is not None else []
        paths = [f.get_path() for f in files if f.get_path()]
        if not paths:
            return False
        self._start_custom(paths)
        return True

    def _start_custom(self, paths):
        paths = [p for p in paths if p]
        if paths:
            self.win.start_scan("custom", paths, elevated=self.admin_switch.get_active())

    # ==================================================================== running
    def _build_running(self):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, valign=Gtk.Align.CENTER)
        column.set_vexpand(True)

        self.run_badge, self.run_icon = common.hero_badge("system-search-symbolic", 56)
        common.set_state_class(self.run_badge, "busy")
        self.run_badge.set_margin_bottom(8)
        column.append(self.run_badge)
        self.run_title = common.label("Scanning…", ("title-1",), xalign=0.5)
        column.append(self.run_title)
        self.run_sub = common.label("", ("dim-label",), xalign=0.5, wrap=True, justify=Gtk.Justification.CENTER)
        column.append(self.run_sub)

        self.progress = Gtk.ProgressBar()
        self.progress.set_margin_top(14)
        self.progress.add_css_class("scan-progress")
        column.append(self.progress)
        self.current = common.label("", ("caption", "dim-label", "monospace"), xalign=0.5,
                                    ellipsize=common.ellipsize_middle())
        column.append(self.current)

        stats = Gtk.Box(spacing=12, homogeneous=True)
        stats.set_margin_top(12)
        stats.add_css_class("stats-row")
        box, self.stat_files = common.stat_box("files checked")
        stats.append(box)
        box, self.stat_threats = common.stat_box("threats")
        self.stat_threats_box = box
        stats.append(box)
        box, self.stat_time = common.stat_box("elapsed")
        stats.append(box)
        column.append(stats)

        self.live_group = Adw.PreferencesGroup(title="Found so far")
        self.live_group.set_margin_top(10)
        self.live_group.set_visible(False)
        self._live_row_widgets = []
        column.append(self.live_group)

        self.run_shutdown_check = Gtk.CheckButton(label="Shut down the computer when the scan is done",
                                                  halign=Gtk.Align.CENTER)
        self.run_shutdown_check.set_margin_top(10)
        self.run_shutdown_check.connect("toggled", self._on_shutdown_toggled)
        self.run_shutdown_check.set_visible(False)
        column.append(self.run_shutdown_check)

        self.stop_btn = common.button("Stop Scan", "media-playback-stop-symbolic", ("pill", "destructive-action"),
                                      on_click=self._confirm_stop)
        self.stop_btn.set_halign(Gtk.Align.CENTER)
        self.stop_btn.set_margin_top(14)
        column.append(self.stop_btn)

        return common.scrolled_clamp(column, 620, 28)

    def _on_started(self, job):
        self._live_rows = 0
        for r in self._live_row_widgets:
            self.live_group.remove(r)
        self._live_row_widgets = []
        self.live_group.set_visible(False)
        self.run_title.set_text("Loading virus definitions…")
        self.run_sub.set_text(KIND_DESCRIPTIONS[job.kind] + (" · as administrator" if job.elevated else ""))
        self.current.set_text("ClamAV loads its signature database before the first file - this takes a moment.")
        self.progress.set_fraction(0)
        self.stat_files.set_text("0")
        self.stat_threats.set_text("0")
        common.set_state_class(self.stat_threats_box, None)
        self.stat_time.set_text("0 s")
        self.run_shutdown_check.set_visible(job.kind == "full")
        self._sync_shutdown(self.win.shutdown_after)
        self.stop_btn.set_sensitive(True)
        self.stop_btn.set_child(Adw.ButtonContent(label="Stop Scan", icon_name="media-playback-stop-symbolic"))
        self.stack.set_visible_child_name("running")

    def _on_progress(self, job):
        with job.lock:
            stats = job.stats
            files = stats.files_scanned
            current = stats.current
            ready = stats.engine_ready
            threats = list(stats.threats[self._live_rows:])
            total = job.total_files
            elapsed = stats.elapsed
        self.stat_files.set_text(f"{files:,}")
        self.stat_time.set_text(clamav.human_duration(elapsed))
        n = self._live_rows + len(threats)
        self.stat_threats.set_text(str(n))
        if n:
            common.set_state_class(self.stat_threats_box, "error")
        if ready:
            if job.cancel_requested:
                self.run_title.set_text("Stopping…")
            else:
                self.run_title.set_text("Scanning…")
            self.current.set_text(clamav.display_text(current))
            if total:
                frac = min(files / total, 0.99)
                self.progress.set_fraction(frac)
                self.progress.set_text(f"about {int(frac * 100)}%")
                self.progress.set_show_text(True)
            else:
                self.progress.pulse()
                self.progress.set_show_text(False)
        else:
            self.progress.pulse()
        for path, sig in threats:
            self._live_rows += 1
            if len(self._live_row_widgets) < MAX_LIVE_ROWS:
                r = common.row(os.path.basename(path) or path, f"{sig}\n{common.short_path(path, 4)}",
                               "dialog-warning-symbolic", ("error",), subtitle_lines=2)
                self.live_group.add(r)
                self._live_row_widgets.append(r)
            self.live_group.set_visible(True)

    def _on_shutdown_toggled(self, check):
        self.win.set_shutdown_after(check.get_active())

    def _sync_shutdown(self, value):
        for check in (self.shutdown_check, self.run_shutdown_check):
            if check.get_active() != value:
                check.set_active(value)

    def _confirm_stop(self):
        job = self.win.scan_job
        if not job or not job.running:
            return

        def answer(resp):
            if resp == "stop":
                self.stop_btn.set_sensitive(False)
                self.run_title.set_text("Stopping…")
                self.win.cancel_scan()

        common.alert(self.win, "Stop this scan?",
                     "Threats found so far will still be shown so you can deal with them.",
                     [("cancel", "Keep Scanning"), ("stop", "Stop Scan")], destructive="stop", callback=answer)

    def _on_finished(self, result):
        if result is None:   # authentication dismissed
            self.stack.set_visible_child_name("choose")
            return
        self.show_result(result)

    # ==================================================================== results
    def _build_results(self):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=26)

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.CENTER)
        self.res_badge, self.res_icon = common.hero_badge("security-high-symbolic", 56)
        self.res_badge.set_margin_bottom(8)
        head.append(self.res_badge)
        self.res_title = common.label("", ("title-1",), xalign=0.5, wrap=True, justify=Gtk.Justification.CENTER)
        head.append(self.res_title)
        self.res_sub = common.label("", ("dim-label",), xalign=0.5, wrap=True, justify=Gtk.Justification.CENTER)
        self.res_sub.set_max_width_chars(60)
        head.append(self.res_sub)
        column.append(head)

        stats = Gtk.Box(spacing=12, homogeneous=True)
        stats.add_css_class("stats-row")
        box, self.res_files = common.stat_box("files checked")
        stats.append(box)
        box, self.res_threats = common.stat_box("threats")
        self.res_threats_box = box
        stats.append(box)
        box, self.res_time = common.stat_box("duration")
        stats.append(box)
        box, self.res_errors = common.stat_box("couldn't be read")
        stats.append(box)
        column.append(stats)

        self.threat_group = Adw.PreferencesGroup(title="Threats")
        self.threat_group.set_description("Quarantine locks a file away where it can't run - you can still restore "
                                          "it later. Delete removes it for good.")
        actions = Gtk.Box(spacing=6)
        self.quarantine_all = common.button("Quarantine All", "changes-prevent-symbolic", ("suggested-action",),
                                            on_click=self._quarantine_all)
        self.delete_all = common.button(icon_name="user-trash-symbolic", css=("flat",),
                                        tooltip="Delete all threats permanently", on_click=self._delete_all)
        actions.append(self.delete_all)
        actions.append(self.quarantine_all)
        self.threat_group.set_header_suffix(actions)
        self._threat_rows = []
        column.append(self.threat_group)

        self.error_group = Adw.PreferencesGroup()
        self.error_expander = Adw.ExpanderRow()
        self.error_expander.set_use_markup(False)
        self.error_expander.add_prefix(common.icon("dialog-warning-symbolic", 16, ("warning",)))
        self._error_rows = []
        self.error_group.add(self.error_expander)
        column.append(self.error_group)

        done = common.button("New Scan", "go-previous-symbolic", ("pill",),
                             on_click=lambda: self.stack.set_visible_child_name("choose"))
        done.set_halign(Gtk.Align.CENTER)
        column.append(done)

        return common.scrolled_clamp(column, 820, 32)

    def show_result(self, result):
        if not result:
            self.stack.set_visible_child_name("choose")
            return
        self.result = result
        self._fill_results(result)
        self.stack.set_visible_child_name("results")

    def _fill_results(self, result):
        outcome = result.get("outcome")
        threats = result.get("threats", [])
        name = KIND_NAMES.get(result.get("kind"), "Scan")
        when = clamav.relative_time(clamav.parse_iso(result.get("finished")))
        open_threats = [t for t in threats if t.get("status") in ("pending", "failed")]

        if outcome == "failed":
            common.set_state_class(self.res_badge, "error")
            self.res_icon.set_from_icon_name("dialog-error-symbolic")
            self.res_title.set_text("The scan couldn't run")
            self.res_sub.set_text(result.get("reason") or "ClamAV reported an error.")
        elif threats and open_threats:
            n = len(open_threats)
            common.set_state_class(self.res_badge, "error")
            self.res_icon.set_from_icon_name("security-low-symbolic")
            self.res_title.set_text(f"{n} threat{'s' if n != 1 else ''} need{'s' if n == 1 else ''} attention")
            self.res_sub.set_text(f"{name} · {when}. Quarantine them to be safe.")
        elif threats:
            common.set_state_class(self.res_badge, "ok")
            self.res_icon.set_from_icon_name("security-high-symbolic")
            self.res_title.set_text("All threats handled")
            self.res_sub.set_text(f"{name} · {when}. Nothing else needs your attention.")
        elif outcome == "cancelled":
            common.set_state_class(self.res_badge, "warning")
            self.res_icon.set_from_icon_name("security-medium-symbolic")
            self.res_title.set_text("Scan stopped")
            self.res_sub.set_text("No threats were found before the scan was stopped.")
        else:
            common.set_state_class(self.res_badge, "ok")
            self.res_icon.set_from_icon_name("security-high-symbolic")
            self.res_title.set_text("No threats found")
            self.res_sub.set_text(f"{name} · {when}. Everything checked is clean.")

        self.res_files.set_text(f"{result.get('files', 0):,}")
        self.res_threats.set_text(str(len(threats)))
        common.set_state_class(self.res_threats_box, "error" if open_threats else None)
        self.res_time.set_text(clamav.human_duration(result.get("duration", 0)))
        self.res_errors.set_text(f"{result.get('errors', 0):,}")

        # threats
        common.clear_group(self.threat_group, self._threat_rows)
        self.threat_group.set_visible(bool(threats))
        self.quarantine_all.set_visible(bool(open_threats))
        self.delete_all.set_visible(bool(open_threats))
        for t in threats:
            r = self._threat_row(t)
            self.threat_group.add(r)
            self._threat_rows.append(r)
        self._apply_busy()

        # files that couldn't be read
        for r in self._error_rows:
            self.error_expander.remove(r)
        self._error_rows = []
        errors = result.get("errors", 0)
        samples = result.get("error_samples", [])
        self.error_group.set_visible(bool(errors))
        if errors:
            self.error_expander.set_title(f"{errors:,} item{'s' if errors != 1 else ''} couldn't be scanned")
            self.error_expander.set_subtitle(
                "Usually files you don't have permission to read. Turn on administrator rights to include them."
                if result.get("kind") != "full" else "Usually special or locked system files.")
            for path, msg in samples[:200]:
                r = common.row(common.short_path(path, 4), msg)
                self.error_expander.add_row(r)
                self._error_rows.append(r)

    def _threat_row(self, t):
        path = t["path"]
        r = common.row(os.path.basename(path) or path, f"{t['signature']}\n{clamav.display_text(path)}",
                       subtitle_lines=3)
        status = t.get("status", "pending")
        if status in ("pending", "failed"):
            r.add_prefix(common.icon("dialog-warning-symbolic", 16, ("error",)))
            box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
            if status == "failed" and t.get("error"):
                err = common.label(t["error"], ("caption", "error"))
                err.set_max_width_chars(18)
                err.set_wrap(True)
                box.append(err)
            menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic")
            menu_btn.add_css_class("flat")
            menu_btn.set_tooltip_text("More actions")
            menu_btn.set_popover(self._threat_popover(t))
            box.append(menu_btn)
            q = common.button("Quarantine", css=("flat",), on_click=lambda: self.win.quarantine_threats([t]))
            box.append(q)
            r.threat_buttons = (q, menu_btn)
            r.add_suffix(box)
        else:
            text, css = STATUS_TEXT.get(status, (status.title(), "dim"))
            r.add_prefix(common.icon("emblem-ok-symbolic" if css == "ok" else "action-unavailable-symbolic",
                                     16, (css,)))
            r.add_suffix(common.label(text, ("caption", css if css != "dim" else "dim-label")))
            r.threat_buttons = ()
        return r

    def _threat_popover(self, t):
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)

        def item(text, icon_name, cb, css=()):
            b = common.button(text, icon_name, ("flat",) + tuple(css))
            b.get_child().set_halign(Gtk.Align.START)
            b.connect("clicked", lambda *_: (pop.popdown(), cb()))
            box.append(b)

        item("Delete Permanently", "user-trash-symbolic", lambda: self._confirm_delete([t]), ("error",))
        item("Open Containing Folder", "folder-open-symbolic", lambda: self._open_folder(t["path"]))
        item("Copy Path", "edit-copy-symbolic", lambda: self._copy(t["path"]))
        item("Ignore (Leave in Place)", "action-unavailable-symbolic", lambda: self._confirm_ignore(t))
        pop.set_child(box)
        return pop

    def _open_folder(self, path):
        from gi.repository import Gio
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(path))
        launcher.open_containing_folder(self.win, None, None)

    def _copy(self, path):
        Gdk.Display.get_default().get_clipboard().set(clamav.display_text(path))
        self.win.toast("Path copied")

    def _quarantine_all(self):
        if self.result:
            self.win.quarantine_threats(self.result.get("threats", []))

    def _delete_all(self):
        if self.result:
            self._confirm_delete([t for t in self.result.get("threats", []) if t.get("status") in ("pending", "failed")])

    def _confirm_delete(self, threats):
        if not threats:
            return
        n = len(threats)
        body = ("This permanently removes "
                + (f"“{os.path.basename(clamav.display_text(threats[0]['path']))}”" if n == 1 else f"{n} files")
                + " from your computer. It can't be undone.\n\nIf you're not sure, quarantine instead - "
                  "you can delete from the quarantine later.")
        common.alert(self.win, "Delete permanently?", body,
                     [("cancel", "Cancel"), ("delete", "Delete")], destructive="delete",
                     callback=lambda r: r == "delete" and self.win.delete_threats(threats))

    def _confirm_ignore(self, t):
        common.alert(self.win, "Leave this file in place?",
                     f"“{os.path.basename(clamav.display_text(t['path']))}” was detected as {t['signature']}. "
                     "Only ignore it if you're sure it's a false alarm.",
                     [("cancel", "Cancel"), ("ignore", "Ignore")], destructive="ignore",
                     callback=lambda r: r == "ignore" and self.win.ignore_threat(t))

    def _on_busy(self, busy):
        self._busy = busy
        self._apply_busy()

    def _apply_busy(self):
        self.quarantine_all.set_sensitive(not self._busy)
        self.delete_all.set_sensitive(not self._busy)
        for r in self._threat_rows:
            for b in getattr(r, "threat_buttons", ()):
                b.set_sensitive(not self._busy)


def files_from_model(model):
    paths = []
    for i in range(model.get_n_items()):
        f = model.get_item(i)
        if f is not None and f.get_path():
            paths.append(f.get_path())
    return paths


def iter_children(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()
