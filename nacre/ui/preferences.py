"""Preferences dialog. Every change is written to the activity log."""

from gi.repository import Adw, GLib, Gtk

from ..settings import LABELS
from . import common


def build_preferences(win):
    s = win.settings
    dlg = Adw.PreferencesDialog()
    dlg.set_search_enabled(False)

    page = Adw.PreferencesPage(title="Scanning", icon_name="system-search-symbolic")
    dlg.add(page)

    def switch(group, key, subtitle):
        row = Adw.SwitchRow(title=LABELS[key], subtitle=subtitle)
        row.set_active(bool(s.get(key)))

        def changed(r, _pspec):
            value = r.get_active()
            if value != s.get(key):
                s.set(key, value)
                win.log.add("settings", f"{LABELS[key]} turned {'on' if value else 'off'}", "info")
        row.connect("notify::active", changed)
        group.add(row)
        return row

    detection = Adw.PreferencesGroup(title="Detection")
    switch(detection, "scan_archives", "Look inside .zip, .tar, .7z and other archives")
    switch(detection, "detect_pua", "Also flag adware, crypto miners and hacking tools - may report some false alarms")
    size = Adw.SpinRow.new_with_range(10, 4000, 10)
    size.set_title(LABELS["max_filesize_mb"])
    size.set_subtitle("Bigger files are skipped. Higher values find more but scan slower")
    size.set_value(int(s.get("max_filesize_mb")))
    pending = {"id": 0}

    def size_changed(r, _pspec):
        if pending["id"]:
            GLib.source_remove(pending["id"])

        def commit():
            pending["id"] = 0
            value = int(r.get_value())
            if value != s.get("max_filesize_mb"):
                s.set("max_filesize_mb", value)
                win.log.add("settings", f"{LABELS['max_filesize_mb']} set to {value} MB", "info")
            return GLib.SOURCE_REMOVE

        pending["id"] = GLib.timeout_add(800, commit)
    size.connect("notify::value", size_changed)
    detection.add(size)
    page.add(detection)

    perf = Adw.PreferencesGroup(title="Performance")
    switch(perf, "low_priority", "Scan with lower CPU and disk priority so games and apps stay smooth")
    switch(perf, "include_external", "Also check USB drives and disks mounted in /media and /mnt")
    page.add(perf)

    after = Adw.PreferencesGroup(title="After a scan")
    switch(after, "auto_quarantine", "Move threats to the quarantine right away (you'll be asked for your password)")
    switch(after, "notify_on_finish", "Show a desktop notification when Nacre is in the background")
    page.add(after)

    excl = Adw.PreferencesGroup(title="Excluded folders",
                                description="Nacre skips these folders in every scan. Only exclude places you trust.")
    rows = []

    def rebuild():
        common.clear_group(excl, rows)
        for folder in s.get("exclusions") or []:
            r = common.row(folder, None, "folder-symbolic")
            rm = common.button(icon_name="list-remove-symbolic", css=("flat",), tooltip="Remove",
                               on_click=lambda f=folder: remove(f))
            rm.set_valign(Gtk.Align.CENTER)
            r.add_suffix(rm)
            excl.add(r)
            rows.append(r)
        add = Adw.ActionRow(title="Add Folder…")
        add.add_prefix(common.icon("list-add-symbolic"))
        add.set_activatable(True)
        add.connect("activated", lambda *_: choose())
        excl.add(add)
        rows.append(add)

    def remove(folder):
        folders = [f for f in s.get("exclusions") or [] if f != folder]
        s.set("exclusions", folders)
        win.log.add("settings", "Folder removed from exclusions", "info", folder=folder)
        rebuild()

    def choose():
        fd = Gtk.FileDialog(title="Choose a folder to exclude", modal=True)

        def done(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            path = f.get_path()
            if not path:
                return
            folders = list(s.get("exclusions") or [])
            if path not in folders:
                folders.append(path)
                s.set("exclusions", folders)
                win.log.add("settings", "Folder added to exclusions", "warning", folder=path)
            rebuild()

        fd.select_folder(win, None, done)

    rebuild()
    page.add(excl)
    return dlg
