"""Dialog that shows freshclam updating the virus definitions."""

from gi.repository import Adw, Gtk

from . import common


class UpdateDialog(Adw.Dialog):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.set_title("Update Virus Definitions")
        self.set_content_width(600)
        self.set_content_height(470)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.set_margin_top(6)
        body.set_margin_bottom(18)
        body.set_margin_start(18)
        body.set_margin_end(18)

        status = Gtk.Box(spacing=12)
        self.status_stack = Gtk.Stack()
        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(24, 24)
        self.spinner.start()
        self.status_stack.add_named(self.spinner, "spinner")
        self.result_icon = common.icon("emblem-ok-symbolic", 24)
        self.status_stack.add_named(self.result_icon, "icon")
        status.append(self.status_stack)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.status_title = common.label("Updating virus definitions…", ("title-4",))
        self.status_sub = common.label(
            "Waiting for your password, then ClamAV's freshclam downloads the newest signatures. "
            "This usually takes under a minute.", ("dim-label",), wrap=True)
        text.append(self.status_title)
        text.append(self.status_sub)
        text.set_hexpand(True)
        status.append(text)
        body.append(status)

        self.buffer = Gtk.TextBuffer()
        view = Gtk.TextView(buffer=self.buffer, editable=False, cursor_visible=False, monospace=True)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_top_margin(10)
        view.set_bottom_margin(10)
        view.set_left_margin(12)
        view.set_right_margin(12)
        view.add_css_class("update-output")
        self.view = view
        sw = Gtk.ScrolledWindow(child=view, vexpand=True)
        sw.add_css_class("card")
        sw.set_overflow(Gtk.Overflow.HIDDEN)
        self.scroller = sw
        body.append(sw)

        self.close_btn = common.button("Close", css=("pill",), on_click=self.close)
        self.close_btn.set_halign(Gtk.Align.CENTER)
        body.append(self.close_btn)

        view_root = Adw.ToolbarView()
        view_root.add_top_bar(Adw.HeaderBar())
        view_root.set_content(body)
        self.set_child(view_root)

    def append(self, line):
        end = self.buffer.get_end_iter()
        self.buffer.insert(end, line + "\n")
        mark = self.buffer.create_mark(None, self.buffer.get_end_iter(), False)
        self.view.scroll_mark_onscreen(mark)
        self.buffer.delete_mark(mark)

    def replay(self, lines):
        self.buffer.set_text("")
        for line in lines:
            self.append(line)

    def finish(self, status, message):
        self.spinner.stop()
        self.status_stack.set_visible_child_name("icon")
        if status in ("updated", "current"):
            self.result_icon.set_from_icon_name("emblem-ok-symbolic")
            common.set_state_class(self.result_icon, "ok")
            self.status_title.set_text(message)
            self.status_sub.set_text("ClamAV is using the newest signatures.")
        elif status == "auth":
            self.result_icon.set_from_icon_name("dialog-warning-symbolic")
            common.set_state_class(self.result_icon, "warning")
            self.status_title.set_text("Update cancelled")
            self.status_sub.set_text(message)
        else:
            self.result_icon.set_from_icon_name("dialog-error-symbolic")
            common.set_state_class(self.result_icon, "error")
            self.status_title.set_text("Update failed")
            self.status_sub.set_text(message)
        self.close_btn.add_css_class("suggested-action")
