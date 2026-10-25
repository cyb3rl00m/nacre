"""Quarantine page: everything Nacre has locked away."""

import os

from gi.repository import Adw, Gtk

from .. import clamav
from . import common


class QuarantinePage(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self._rows = []
        self._busy = False

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)

        empty = Adw.StatusPage(
            icon_name="security-high-symbolic",
            title="Quarantine is empty",
            description="Threats you quarantine after a scan are locked away here, where they can't run.",
        )
        empty.add_css_class("compact")
        self.stack.add_named(empty, "empty")

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head.append(common.label("Quarantined files", ("title-2",)))
        head.append(common.label(
            "These files were moved out of reach and stored scrambled, so they can't run or be opened. "
            "Delete them for good, or restore one if you're sure it was a false alarm.",
            ("dim-label",), wrap=True))
        column.append(head)

        self.group = Adw.PreferencesGroup()
        actions = Gtk.Box(spacing=6)
        actions.append(common.button(icon_name="view-refresh-symbolic", css=("flat",), tooltip="Refresh",
                                     on_click=win.refresh_quarantine))
        self.delete_all_btn = common.button("Delete All", "user-trash-symbolic", ("destructive-action",),
                                            on_click=self._confirm_delete_all)
        actions.append(self.delete_all_btn)
        self.group.set_header_suffix(actions)
        column.append(self.group)

        self.stack.add_named(common.scrolled_clamp(column, 860, 28), "list")
        self.append(self.stack)

        win.bus.listen("quarantine", self.refresh)
        win.bus.listen("busy", self._on_busy)
        self.refresh()

    def on_shown(self):
        self.win.refresh_quarantine()

    def refresh(self):
        items = self.win.quarantine
        common.clear_group(self.group, self._rows)
        if not items:
            self.stack.set_visible_child_name("empty")
            return
        total = sum(int(i.get("size", 0)) for i in items)
        n = len(items)
        self.group.set_title(f"{n} item{'s' if n != 1 else ''} · {clamav.human_size(total)}")
        for item in items:
            r = self._item_row(item)
            self.group.add(r)
            self._rows.append(r)
        self.stack.set_visible_child_name("list")
        self._on_busy(self._busy)

    def _item_row(self, item):
        path = item.get("original_path", "")
        when = clamav.parse_iso(item.get("quarantined_at"))
        when_text = f"{when:%d %b %Y, %H:%M}" if when else "unknown date"
        subtitle = (f"{item.get('signature', 'Unknown')} · {clamav.human_size(item.get('size', 0))} · {when_text}\n"
                    f"{clamav.display_text(path)}")
        r = common.row(os.path.basename(path) or path, subtitle, "changes-prevent-symbolic", ("warning",),
                       subtitle_lines=3)
        box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        restore = common.button(icon_name="edit-undo-symbolic", css=("flat",), tooltip="Restore to original location",
                                on_click=lambda: self._confirm_restore(item))
        delete = common.button(icon_name="user-trash-symbolic", css=("flat", "error"), tooltip="Delete permanently",
                               on_click=lambda: self._confirm_delete(item))
        box.append(restore)
        box.append(delete)
        r.add_suffix(box)
        r.item_buttons = (restore, delete)
        return r

    def _confirm_restore(self, item):
        name = os.path.basename(clamav.display_text(item.get("original_path", "")))
        common.alert(
            self.win, "Restore this file?",
            f"“{name}” was detected as {item.get('signature', 'a threat')}. Restoring puts it back in\n"
            f"{clamav.display_text(os.path.dirname(item.get('original_path', '')))}\n\n"
            "Only do this if you're sure it's a false alarm. Consider adding the folder to the exclusions "
            "in Preferences so it isn't flagged again.",
            [("cancel", "Cancel"), ("restore", "Restore")], destructive="restore",
            callback=lambda r: r == "restore" and self.win.restore_items([item]),
        )

    def _confirm_delete(self, item):
        name = os.path.basename(clamav.display_text(item.get("original_path", "")))
        common.alert(
            self.win, "Delete permanently?",
            f"“{name}” will be removed from your computer for good. This can't be undone.",
            [("cancel", "Cancel"), ("delete", "Delete")], destructive="delete",
            callback=lambda r: r == "delete" and self.win.delete_quarantined([item]),
        )

    def _confirm_delete_all(self):
        n = len(self.win.quarantine)
        if not n:
            return
        common.alert(
            self.win, f"Delete all {n} quarantined file{'s' if n != 1 else ''}?",
            "They will be removed from your computer for good. This can't be undone.",
            [("cancel", "Cancel"), ("delete", "Delete All")], destructive="delete",
            callback=lambda r: r == "delete" and self.win.delete_quarantined(everything=True),
        )

    def _on_busy(self, busy):
        self._busy = busy
        self.delete_all_btn.set_sensitive(not busy)
        for r in self._rows:
            for b in r.item_buttons:
                b.set_sensitive(not busy)
