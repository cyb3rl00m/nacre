"""Application entry point."""

import sys

import gi

try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError:
    sys.stderr.write(
        "Nacre needs GTK 4 and libadwaita.\n"
        "Install them with:  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1\n"
    )
    sys.exit(1)

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, ISSUES, VERSION, WEBSITE, paths  # noqa: E402

MIN_ADW = (1, 5)


class NacreApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        GLib.set_application_name(APP_NAME)
        self.window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)
        self._load_css()
        self._add_actions()

    def _load_css(self):
        display = Gdk.Display.get_default()
        provider = Gtk.CssProvider()
        provider.load_from_path(str(paths.style_path()))
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        # When running from the source tree, make the app icon findable.
        if not paths.is_installed():
            Gtk.IconTheme.get_for_display(display).add_search_path(str(paths.source_icon_dir()))

    def _add_actions(self):
        def add(name, callback, accels=()):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
            if accels:
                self.set_accels_for_action(f"app.{name}", list(accels))

        add("quit", lambda *_: self._quit(), ["<Control>q"])
        add("about", lambda *_: self._about())
        add("preferences", lambda *_: self.window and self.window.show_preferences(), ["<Control>comma"])
        self.set_accels_for_action("win.quick-scan", ["<Control>r"])
        self.set_accels_for_action("win.update", ["<Control>u"])
        self.set_accels_for_action("win.page('overview')", ["<Alt>1"])
        self.set_accels_for_action("win.page('scan')", ["<Alt>2"])
        self.set_accels_for_action("win.page('quarantine')", ["<Alt>3"])
        self.set_accels_for_action("win.page('logs')", ["<Alt>4"])

    def _ensure_window(self):
        if self.window is None:
            from .window import NacreWindow
            self.window = NacreWindow(application=self)
        return self.window

    def do_activate(self):
        self._ensure_window().present()

    def do_open(self, files, *_args):
        # PyGObject passes (files, n_files, hint); only the files matter here.
        win = self._ensure_window()
        win.present()
        targets = [f.get_path() for f in files if f.get_path()]
        if targets:
            win.request_custom_scan(targets)

    def _quit(self):
        if self.window:
            self.window.close()
        else:
            self.quit()

    def _about(self):
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            developer_name="cyb3rl00m",
            version=VERSION,
            website=WEBSITE,
            issue_url=ISSUES,
            license_type=Gtk.License.MIT_X11,
            developers=["cyb3rl00m https://github.com/cyb3rl00m"],
            comments="A modern, friendly antivirus for Linux, powered by the ClamAV engine.",
        )
        about.add_legal_section(
            "ClamAV",
            "Copyright © Cisco Systems, Inc.",
            Gtk.License.GPL_2_0_ONLY,
            None,
        )
        about.present(self.window)


def main(argv=None):
    version = (Adw.get_major_version(), Adw.get_minor_version())
    if version < MIN_ADW:
        sys.stderr.write(
            f"Nacre needs libadwaita {MIN_ADW[0]}.{MIN_ADW[1]} or newer "
            f"(found {version[0]}.{version[1]}). Ubuntu 24.04+, Debian 13+ and Pop!_OS 24.04+ have it.\n"
        )
        return 1
    app = NacreApp()
    return app.run(argv if argv is not None else sys.argv)
