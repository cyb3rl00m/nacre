"""A permissive stand-in for PyGObject so the GUI glue code can be exercised
in environments without GTK (CI containers). It cannot validate GTK API
usage - it catches Python-level mistakes in Nacre's own code.
"""

import sys
import types


class _Meta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        # enum values / constants / static constructors
        return _FakeStatic(f"{cls.__name__}.{name}")


class _FakeStatic:
    def __init__(self, name):
        self._name = name

    def __call__(self, *args, **kwargs):
        if self._name.endswith(".new") or ".new_" in self._name:
            cls_name = self._name.split(".")[0]
            return _registry_get(cls_name)()
        return _FakeStatic(self._name + "()")

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _FakeStatic(f"{self._name}.{name}")

    def __or__(self, other):
        return self

    def __repr__(self):
        return f"<{self._name}>"


class FakeObject(metaclass=_Meta):
    _signals = None

    def __init__(self, *args, **kwargs):
        object.__setattr__(self, "_props", dict(kwargs))
        object.__setattr__(self, "_handlers", {})
        object.__setattr__(self, "_children", [])
        object.__setattr__(self, "_named", {})
        object.__setattr__(self, "_visible_name", None)

    # signals
    def connect(self, signal, cb, *extra):
        self._handlers.setdefault(signal, []).append((cb, extra))
        return len(self._handlers)

    def fire(self, signal, *args):
        for cb, extra in self._handlers.get(signal, []):
            cb(self, *args, *extra)

    # containers
    def append(self, child, *_rest):
        self._children.append(child)

    add = add_row = add_prefix = add_suffix = set_child = append

    def insert_child_after(self, child, _sibling):
        self._children.append(child)

    def remove(self, child):
        if child in self._children:
            self._children.remove(child)

    def get_child(self):
        return self._children[-1] if self._children else None

    def get_first_child(self):
        return self._children[0] if self._children else None

    def add_named(self, child, name):
        self._named[name] = child
        if self._visible_name is None:
            object.__setattr__(self, "_visible_name", name)

    def set_visible_child_name(self, name):
        assert name in self._named, f"unknown stack page {name}"
        object.__setattr__(self, "_visible_name", name)

    def get_visible_child_name(self):
        return self._visible_name

    def get_visible_child(self):
        return self._named.get(self._visible_name)

    def get_child_by_name(self, name):
        return self._named.get(name)

    # values
    def set_text(self, text):
        assert isinstance(text, str), f"set_text needs str, got {type(text)}"
        self._props["text"] = text

    def get_text(self):
        return self._props.get("text", "")

    def set_title(self, t):
        assert isinstance(t, str), f"set_title needs str, got {type(t)}"
        self._props["title"] = t

    def set_subtitle(self, t):
        assert isinstance(t, str), f"set_subtitle needs str, got {type(t)}"
        self._props["subtitle"] = t

    def set_label(self, t):
        assert isinstance(t, str), f"set_label needs str, got {type(t)}"
        self._props["label"] = t

    def get_selected(self):
        return 0

    def get_active(self):
        return bool(self._props.get("active", False))

    def set_active(self, v):
        self._props["active"] = v

    def get_value(self):
        return self._props.get("value", 100)

    def set_value(self, v):
        self._props["value"] = v

    def is_active(self):
        return False

    def get_application(self):
        return FakeObject()

    def select_row(self, row):
        self.fire("row-selected", row)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def method(*args, **kwargs):
            return None
        return method


_registry = {}


def _registry_get(name):
    if name not in _registry:
        _registry[name] = _Meta(name, (FakeObject,), {})
    return _registry[name]


class _FakeModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _registry_get(name)


class GLibError(Exception):
    pass


def install():
    gi = types.ModuleType("gi")
    gi.require_version = lambda *a: None
    repo = types.ModuleType("gi.repository")
    gi.repository = repo
    mods = {}
    for name in ("Gtk", "Adw", "Gio", "Gdk", "Pango", "GObject"):
        mods[name] = _FakeModule(f"gi.repository.{name}")
        setattr(repo, name, mods[name])
        sys.modules[f"gi.repository.{name}"] = mods[name]

    glib = _FakeModule("gi.repository.GLib")
    glib.SOURCE_CONTINUE = True
    glib.SOURCE_REMOVE = False
    glib.Error = GLibError
    glib.timers = []
    glib.idle_add = lambda fn, *a: fn(*a)
    glib.timeout_add = lambda ms, fn, *a: glib.timers.append((fn, a)) or len(glib.timers)
    glib.timeout_add_seconds = lambda s, fn, *a: glib.timers.append((fn, a)) or len(glib.timers)
    glib.source_remove = lambda _id: None
    glib.get_user_special_dir = lambda *_: None
    glib.set_application_name = lambda *_: None
    repo.GLib = glib
    sys.modules["gi.repository.GLib"] = glib

    mods["Adw"].get_major_version = lambda: 1
    mods["Adw"].get_minor_version = lambda: 5
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repo
    return glib
