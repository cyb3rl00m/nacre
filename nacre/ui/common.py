"""Small widget helpers shared by the pages."""

import os

from gi.repository import Adw, Gtk, Pango

from ..clamav import display_text

STATE_CLASSES = ("ok", "warning", "error", "neutral", "busy")


def label(text="", css=(), xalign=0.0, wrap=False, ellipsize=None, selectable=False, justify=None):
    lbl = Gtk.Label(label=text, xalign=xalign)
    for c in css:
        lbl.add_css_class(c)
    if wrap:
        lbl.set_wrap(True)
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if ellipsize:
        lbl.set_ellipsize(ellipsize)
    if selectable:
        lbl.set_selectable(True)
    if justify is not None:
        lbl.set_justify(justify)
    return lbl


def icon(name, size=16, css=()):
    img = Gtk.Image.new_from_icon_name(name)
    img.set_pixel_size(size)
    for c in css:
        img.add_css_class(c)
    return img


def button(text=None, icon_name=None, css=(), tooltip=None, on_click=None):
    btn = Gtk.Button()
    if text and icon_name:
        content = Adw.ButtonContent(label=text, icon_name=icon_name)
        content.set_use_underline(True)
        btn.set_child(content)
    elif icon_name:
        btn.set_icon_name(icon_name)
    else:
        btn.set_label(text or "")
        btn.set_use_underline(True)
    for c in css:
        btn.add_css_class(c)
    if tooltip:
        btn.set_tooltip_text(tooltip)
    if on_click:
        btn.connect("clicked", lambda *_: on_click())
    return btn


def row(title, subtitle=None, icon_name=None, icon_css=(), activatable=False, on_activate=None,
        subtitle_lines=0):
    r = Adw.ActionRow()
    r.set_use_markup(False)          # file names may contain & < > - never parse them
    r.set_title(display_text(title))
    if subtitle:
        r.set_subtitle(display_text(subtitle))
    if subtitle_lines:
        r.set_subtitle_lines(subtitle_lines)
    if icon_name:
        r.add_prefix(icon(icon_name, 16, icon_css))
    if activatable or on_activate:
        r.set_activatable(True)
        r.add_suffix(icon("go-next-symbolic", 16, ("dim-label",)))
        if on_activate:
            r.connect("activated", lambda *_: on_activate())
    return r


def set_state_class(widget, state):
    for c in STATE_CLASSES:
        widget.remove_css_class(c)
    if state:
        widget.add_css_class(state)


def clear_box(box):
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def clear_group(group, rows):
    for r in rows:
        group.remove(r)
    rows.clear()


def scrolled_clamp(child, max_width=820, margin=24):
    """A scrolled, centred column - the standard page layout."""
    clamp = Adw.Clamp(maximum_size=max_width, tightening_threshold=max_width - 200)
    clamp.set_margin_top(margin)
    clamp.set_margin_bottom(margin)
    clamp.set_margin_start(16)
    clamp.set_margin_end(16)
    clamp.set_child(child)
    sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
    sw.set_vexpand(True)
    sw.set_child(clamp)
    return sw


def hero_badge(icon_name, size=64):
    """A big round tinted badge with an icon in the middle."""
    box = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    box.add_css_class("hero-badge")
    img = icon(icon_name, size)
    img.set_hexpand(True)
    img.set_vexpand(True)
    img.set_halign(Gtk.Align.CENTER)
    img.set_valign(Gtk.Align.CENTER)
    box.append(img)
    return box, img


def stat_box(caption):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
    box.add_css_class("stat")
    value = label("0", ("stat-value", "numeric"), xalign=0.5)
    cap = label(caption, ("caption", "dim-label"), xalign=0.5)
    box.append(value)
    box.append(cap)
    return box, value


def alert(parent, heading, body, responses, default=None, destructive=None, suggested=None,
          callback=None, extra_child=None):
    """Show an Adw.AlertDialog. responses = [(id, label), ...]; the first one is 'cancel'."""
    dlg = Adw.AlertDialog.new(heading, body)
    for rid, text in responses:
        dlg.add_response(rid, text)
    if destructive:
        dlg.set_response_appearance(destructive, Adw.ResponseAppearance.DESTRUCTIVE)
    if suggested:
        dlg.set_response_appearance(suggested, Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response(default or responses[0][0])
    dlg.set_close_response(responses[0][0])
    if extra_child is not None:
        dlg.set_extra_child(extra_child)
    if callback:
        dlg.connect("response", lambda _d, resp: callback(resp))
    dlg.present(parent)
    return dlg


def short_path(path, keep=3):
    """'/home/me/a/b/c/file' -> '…/b/c/file' for compact subtitles."""
    path = display_text(path)
    home = os.path.expanduser("~")
    if path.startswith(home + "/"):
        path = "~" + path[len(home):]
    parts = path.split("/")
    if len(parts) > keep + 1:
        return "…/" + "/".join(parts[-keep:])
    return path


def ellipsize_middle():
    return Pango.EllipsizeMode.MIDDLE
