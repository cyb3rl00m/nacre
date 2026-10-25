"""Activity log page: everything Nacre did in the last 30 days."""

import datetime

from gi.repository import Adw, Gio, GLib, Gtk

from ..activity_log import CATEGORIES, RETENTION_DAYS
from . import common

LEVEL_ICONS = {
    "info": ("dialog-information-symbolic", "accent"),
    "success": ("emblem-ok-symbolic", "ok"),
    "warning": ("dialog-warning-symbolic", "warning"),
    "error": ("dialog-error-symbolic", "error"),
}
FILTERS = [("all", "All activity")] + [(k, v) for k, v in CATEGORIES.items()]
MAX_SHOWN = 1500


class LogsPage(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self._groups = []
        self._dirty = True
        self._pending = False

        # toolbar
        bar = Gtk.Box(spacing=8)
        bar.add_css_class("logs-toolbar")
        bar.set_margin_top(12)
        bar.set_margin_start(16)
        bar.set_margin_end(16)
        self.search = Gtk.SearchEntry(placeholder_text="Search activity")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", lambda *_: self.refresh())
        bar.append(self.search)
        self.filter = Gtk.DropDown.new_from_strings([label for _, label in FILTERS])
        self.filter.set_tooltip_text("Show only one kind of activity")
        self.filter.connect("notify::selected", lambda *_: self.refresh())
        bar.append(self.filter)

        menu = Gio.Menu()
        menu.append("Export as Text…", "logs.export")
        menu.append("Clear Log…", "logs.clear")
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu)
        more.set_tooltip_text("Log actions")
        bar.append(more)
        actions = Gio.SimpleActionGroup()
        for name, cb in (("export", self._export), ("clear", self._confirm_clear)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, cb=cb: cb())
            actions.add_action(act)
        self.insert_action_group("logs", actions)
        self.append(bar)

        # content
        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.empty = Adw.StatusPage(icon_name="document-open-recent-symbolic", title="No activity yet",
                                    description="Scans, updates and actions on threats will be listed here.")
        self.empty.add_css_class("compact")
        self.stack.add_named(self.empty, "empty")

        self.column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        self.footer = common.label("", ("dim-label", "caption"), xalign=0.5, wrap=True)
        self.column.append(self.footer)
        self.stack.add_named(common.scrolled_clamp(self.column, 900, 20), "list")
        self.append(self.stack)

        win.log.connect(lambda _entry: self._mark_dirty())
        win.bus.listen("log-pruned", self._mark_dirty)

    def on_shown(self):
        if self._dirty:
            self.refresh()

    def _mark_dirty(self):
        self._dirty = True
        if self.win.stack.get_visible_child() is self and not self._pending:
            self._pending = True
            GLib.timeout_add(300, self._deferred_refresh)

    def _deferred_refresh(self):
        self._pending = False
        self.refresh()
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------ filling
    def _filtered(self):
        key = FILTERS[self.filter.get_selected()][0] if self.filter.get_selected() < len(FILTERS) else "all"
        query = self.search.get_text().strip().lower()
        out = []
        for e in self.win.log.entries():
            if key != "all" and e.category != key:
                continue
            if query:
                hay = (e.message + " " + " ".join(f"{k} {v}" for k, v in e.details.items())).lower()
                if query not in hay:
                    continue
            out.append(e)
        return out

    def refresh(self):
        self._dirty = False
        for g in self._groups:
            self.column.remove(g)
        self._groups = []

        entries = self._filtered()
        total_all = len(self.win.log.entries()) if (self.search.get_text() or self.filter.get_selected()) else len(entries)
        if not entries:
            if total_all:
                self.empty.set_title("Nothing matches")
                self.empty.set_description("Try a different search or filter.")
            else:
                self.empty.set_title("No activity yet")
                self.empty.set_description("Scans, updates and actions on threats will be listed here.")
            self.stack.set_visible_child_name("empty")
            return

        shown = entries[:MAX_SHOWN]
        today = datetime.date.today()
        current_day, group = None, None
        for e in shown:
            day = e.time.date()
            if day != current_day:
                current_day = day
                group = Adw.PreferencesGroup(title=day_title(day, today))
                self.column.insert_child_after(group, self._groups[-1] if self._groups else None)
                self._groups.append(group)
            group.add(self._entry_row(e))

        extra = f" Showing the newest {MAX_SHOWN:,}." if len(entries) > MAX_SHOWN else ""
        self.footer.set_text(
            f"{len(entries):,} entr{'ies' if len(entries) != 1 else 'y'}. Nacre keeps {RETENTION_DAYS} days "
            f"of activity - older entries are removed automatically.{extra}")
        self.stack.set_visible_child_name("list")

    def _entry_row(self, e):
        icon_name, css = LEVEL_ICONS.get(e.level, LEVEL_ICONS["info"])
        subtitle = f"{e.time:%H:%M:%S} · {CATEGORIES.get(e.category, e.category)}"
        if not e.details:
            return common.row(e.message, subtitle, icon_name, (css,))
        r = Adw.ExpanderRow()
        r.set_use_markup(False)
        r.set_title(e.message)
        # show the most useful detail right in the subtitle
        lead = e.details.get("file") or e.details.get("reason") or e.details.get("files_checked")
        if lead is not None:
            subtitle += f" · {lead if not isinstance(lead, int) else f'{lead:,} files'}"
        r.set_subtitle(subtitle)
        r.set_subtitle_lines(2)
        r.add_prefix(common.icon(icon_name, 16, (css,)))
        for key, value in e.details.items():
            if isinstance(value, list):
                value = "\n".join(str(v) for v in value)
            elif isinstance(value, int):
                value = f"{value:,}"
            detail = common.row(key.replace("_", " ").capitalize(), str(value))
            detail.set_subtitle_selectable(True)
            r.add_row(detail)
        return r

    # ------------------------------------------------------------------ actions
    def _export(self):
        dlg = Gtk.FileDialog(title="Export activity log", modal=True)
        dlg.set_initial_name(f"nacre-activity-{datetime.date.today():%Y-%m-%d}.txt")

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            path = f.get_path()
            if not path:
                return
            try:
                self.win.log.export_text(path, self._filtered())
            except OSError as exc:
                self.win.toast(f"Export failed: {exc.strerror or exc}")
                return
            self.win.log.add("app", "Activity log exported", "info", file=path)
            self.win.toast("Activity log exported")

        dlg.save(self.win, None, done)

    def _confirm_clear(self):
        def answer(resp):
            if resp == "clear":
                self.win.log.clear()
                self.win.log.add("app", "Activity log cleared by you", "warning")
                self.refresh()

        common.alert(self.win, "Clear the activity log?",
                     "All entries will be removed. This can't be undone.",
                     [("cancel", "Cancel"), ("clear", "Clear Log")], destructive="clear", callback=answer)


def day_title(day, today):
    if day == today:
        return "Today"
    if day == today - datetime.timedelta(days=1):
        return "Yesterday"
    if day.year == today.year:
        return f"{day:%A, %d %B}"
    return f"{day:%d %B %Y}"
