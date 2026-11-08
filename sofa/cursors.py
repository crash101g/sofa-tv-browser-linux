# SPDX-License-Identifier: GPL-3.0-only
"""
A bigger mouse pointer, for reading from the couch.

GTK draws the pointer at the desktop's size. Sofa instead loads the same cursor theme's pictures at a larger size
(XCursor files, which come in several sizes) and hands GTK those as picture cursors. Widgets that ask for a cursor by
name, such as the web page asking for a hand over a link, are watched and given the big one of that name.
"""

import os
import struct

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

SIZES = [(0, "The desktop's"), (48, "Large"), (64, "Extra large"), (96, "Huge")]

_XCURSOR_IMAGE = 0xFFFD0002


def theme_dirs():
    home = os.path.expanduser("~")
    data = [os.path.join(GLib.get_user_data_dir(), "icons"), os.path.join(home, ".icons")]
    data += [os.path.join(d, "icons") for d in GLib.get_system_data_dirs()]
    return data + ["/usr/share/pixmaps"]


def _inherits(theme_path):
    try:
        with open(os.path.join(theme_path, "index.theme"), encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip().lower().startswith("inherits"):
                    return [t.strip() for t in line.split("=", 1)[1].split(",") if t.strip()]
    except OSError:
        pass
    return []


def find_file(theme, name, seen=None):
    """The XCursor file for [name] in [theme] or a theme it inherits from."""
    seen = seen if seen is not None else set()
    if theme in seen:
        return None
    seen.add(theme)
    parents = []
    for d in theme_dirs():
        base = os.path.join(d, theme)
        path = os.path.join(base, "cursors", name)
        if os.path.isfile(path):
            return path
        parents += _inherits(base)
    for p in parents:
        found = find_file(p, name, seen)
        if found:
            return found
    return None


def read_xcursor(data, size):
    """(width, height, xhot, yhot, BGRA premultiplied bytes) of the first image nearest [size], or None."""
    if len(data) < 16 or data[:4] != b"Xcur":
        return None
    _magic, _header, _version, ntoc = struct.unpack_from("<4sIII", data, 0)
    entries = []
    for i in range(ntoc):
        off = 16 + i * 12
        if off + 12 > len(data):
            break
        typ, sub, pos = struct.unpack_from("<III", data, off)
        if typ == _XCURSOR_IMAGE:
            entries.append((sub, pos))
    if not entries:
        return None
    best = min({s for s, _p in entries}, key=lambda s: (abs(s - size), -s))
    pos = next(p for s, p in entries if s == best)  # the first frame of an animated one
    if pos + 36 > len(data):
        return None
    _hsize, _typ, _sub, _ver, w, h, xhot, yhot, _delay = struct.unpack_from("<IIIIIIIII", data, pos)
    pixels = data[pos + 36:pos + 36 + w * h * 4]
    if w == 0 or h == 0 or len(pixels) != w * h * 4:
        return None
    return w, h, xhot, yhot, pixels


# CSS cursor names a page asks for, and the X names themes often use instead.
ALIASES = {
    "default": ["default", "left_ptr", "arrow"],
    "pointer": ["pointer", "hand2", "hand1", "pointing_hand"],
    "text": ["text", "xterm", "ibeam"],
    "vertical-text": ["vertical-text"],
    "wait": ["wait", "watch"],
    "progress": ["progress", "left_ptr_watch"],
    "crosshair": ["crosshair", "cross"],
    "move": ["move", "fleur"],
    "grab": ["grab", "openhand", "hand1"],
    "grabbing": ["grabbing", "closedhand"],
    "not-allowed": ["not-allowed", "crossed_circle"],
    "help": ["help", "question_arrow"],
    "context-menu": ["context-menu"],
    "copy": ["copy"],
    "alias": ["alias"],
    "all-scroll": ["all-scroll", "fleur"],
    "col-resize": ["col-resize", "sb_h_double_arrow"],
    "row-resize": ["row-resize", "sb_v_double_arrow"],
    "zoom-in": ["zoom-in"],
    "zoom-out": ["zoom-out"],
}


class BigCursors:
    """Gives tracked widgets big cursors of the names they ask for. Size 0 leaves them to the desktop."""

    def __init__(self):
        self.size = 0
        self.cache = {}
        self.ours = set()
        self.tracked = []

    def theme(self):
        name = Gtk.Settings.get_default().get_property("gtk-cursor-theme-name")
        return name or "default"

    def cursor(self, name):
        """The big cursor called [name], or None if the theme has no picture for it."""
        key = (self.theme(), name, self.size)
        if key not in self.cache:
            self.cache[key] = self._load(name)
        return self.cache[key]

    def _load(self, name):
        for theme in (self.theme(), "default", "Adwaita"):
            for n in ALIASES.get(name, [name]):
                path = find_file(theme, n)
                if not path:
                    continue
                try:
                    with open(path, "rb") as f:
                        img = read_xcursor(f.read(), self.size)
                except OSError:
                    img = None
                if img is None:
                    continue
                w, h, xh, yh, px = img
                tex = Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED, GLib.Bytes.new(px), w * 4)
                c = Gdk.Cursor.new_from_texture(tex, min(xh, w - 1), min(yh, h - 1), Gdk.Cursor.new_from_name(name))
                self.ours.add(c)
                return c
        return None

    def track(self, widget, name=None):
        """Watch [widget]: whenever it sets a cursor by name, swap in the big one. [name]: a cursor to give it now."""
        widget._sofa_cursor_name = name
        self.tracked.append(widget)
        widget.connect("notify::cursor", self._changed)
        widget.connect("destroy", lambda w: self.tracked.remove(w) if w in self.tracked else None)
        self._apply(widget)

    def track_tree(self, root):
        """[root] gets the big arrow; everything under it that asks for a named cursor (text fields) is watched."""
        self.track(root, "default")
        stack = [root.get_first_child()]
        while stack:
            w = stack.pop()
            while w is not None:
                if w.get_cursor() is not None and w not in self.tracked:
                    self.track(w)
                stack.append(w.get_first_child())
                w = w.get_next_sibling()

    def _changed(self, widget, _p):
        c = widget.get_cursor()
        if c is None or c in self.ours:
            return
        widget._sofa_cursor_name = c.get_name()
        self._apply(widget)

    def _apply(self, widget):
        name = getattr(widget, "_sofa_cursor_name", None)
        if not name:
            return
        if self.size <= 0:
            cur = widget.get_cursor()
            if cur is not None and cur in self.ours:
                widget.set_cursor_from_name(name)
            return
        big = self.cursor(name)
        if big is not None and widget.get_cursor() is not big:
            widget.set_cursor(big)

    def set_size(self, size):
        self.size = int(size or 0)
        for w in list(self.tracked):
            self._apply(w)
