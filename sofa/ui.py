# SPDX-License-Identifier: GPL-3.0-only
"""Sofa's screens and panels: home, the browse menu, search, find in page, the list pages and dialogs."""

import hashlib
import os
import threading
import json
import urllib.request
from urllib.parse import quote

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import paths  # noqa: E402
from .db import host_of, pretty_host  # noqa: E402

TILE_COLOURS = ["#e8590c", "#1c7ed6", "#2f9e44", "#ae3ec9", "#f08c00", "#0c8599", "#d6336c", "#5c7cfa"]


def favicon_path(host):
    return os.path.join(paths.cache_dir(), "favicons", quote(host or "", safe="") + ".png")


def label(text, css=None, xalign=0.0, ellipsize=True, wrap=False):
    lb = Gtk.Label(label=text or "", xalign=xalign)
    if ellipsize:
        lb.set_ellipsize(Pango.EllipsizeMode.END)
    if wrap:
        lb.set_wrap(True)
        lb.set_ellipsize(Pango.EllipsizeMode.NONE)
    if css:
        for c in css.split():
            lb.add_css_class(c)
    return lb


def site_icon(url, size=48, title=None):
    host = host_of(url) or url
    path = favicon_path(host)
    if os.path.exists(path):
        img = Gtk.Image.new_from_file(path)
        img.set_pixel_size(size)
        return img
    letter = label(((title or "").strip() or pretty_host(host) or "?")[:1].upper(), "sofa-tile-letter", xalign=0.5,
                   ellipsize=False)
    colour = TILE_COLOURS[int(hashlib.md5(host.encode()).hexdigest(), 16) % len(TILE_COLOURS)]
    prov = Gtk.CssProvider()
    prov.load_from_string(f"label {{ background-color: {colour}; }}")
    letter.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    letter.set_halign(Gtk.Align.CENTER)
    return letter


def icon(names):
    """An icon from the first of [names] the theme has (a name, or a list of names)."""
    names = [names] if isinstance(names, str) else list(names)
    return Gtk.Image.new_from_gicon(Gio.ThemedIcon.new_from_names(names))


def icon_button(icon_name, text, cb, css="sofa-action"):
    b = Gtk.Button()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    img = icon(icon_name)
    box.append(img)
    lb = label(text, xalign=0.5)
    lb.set_max_width_chars(12)
    lb.set_wrap(True)
    lb.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    lb.set_lines(2)
    lb.set_justify(Gtk.Justification.CENTER)
    box.append(lb)
    b.set_child(box)
    b.add_css_class("flat")
    b.add_css_class(css)
    b.connect("clicked", lambda _b: cb())
    b.icon, b.text = img, lb
    return b


# Home

class Home(Gtk.ScrolledWindow):
    """Search, the top-bar buttons, quick-access tiles, the most visited sites and the bookmarks."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.box.add_css_class("sofa-home")
        self.set_child(self.box)
        self.first_focus = None

    def refresh(self):
        while (c := self.box.get_first_child()) is not None:
            self.box.remove(c)
        a = self.app
        top = Gtk.Box(spacing=12)
        search = Gtk.Button()
        sb = Gtk.Box(spacing=16)
        sb.append(Gtk.Image.new_from_icon_name("system-search-symbolic"))
        hint = label(f"Search with {a.prefs.engine.name} or type an address")
        hint.set_width_chars(24)
        sb.append(hint)
        search.set_child(sb)
        search.add_css_class("sofa-search-pill")
        search.set_hexpand(True)
        search.connect("clicked", lambda _b: a.show_search(""))
        top.append(search)
        self.first_focus = search
        if a.browser.tab and a.browser.tab.uri:
            top.append(icon_button("go-previous-symbolic", "Back to page", a.back_to_page))
        top.append(icon_button("user-bookmarks-symbolic", "Bookmarks", lambda: a.show_list("bookmarks")))
        top.append(icon_button("document-open-recent-symbolic", "History", lambda: a.show_list("history")))
        top.append(icon_button("folder-download-symbolic", "Downloads", lambda: a.show_list("downloads")))
        priv = icon_button("security-high-symbolic" if a.browser.private else "security-medium-symbolic",
                           "End private" if a.browser.private else "Private", a.toggle_private)
        top.append(priv)
        top.append(icon_button("emblem-system-symbolic", "Settings", a.show_settings))
        top.append(icon_button("application-exit-symbolic", "Exit", a.quit))
        self.box.append(top)
        if a.browser.private:
            self.box.append(label("Private browsing: no history is kept, and cookies and site data are erased "
                                  "when it ends.", "sofa-hint", wrap=True))
        tabs = [(i, t) for i, t in enumerate(a.browser.tabs) if t.uri]
        if len(tabs) > 1 or (tabs and a.browser.tab is not None and not a.browser.tab.uri):
            self._section("Open tabs", [(t.title or pretty_host(host_of(t.uri) or t.uri), t.uri) for _i, t in tabs],
                          removable="tab", indexes=[i for i, _t in tabs])

        self._section("Quick access", [(r["title"], r["url"]) for r in a.db.tiles()], removable="tile")
        if a.prefs.most_visited and not a.browser.private:
            mv = [(r["title"] or pretty_host(host_of(r["url"]) or r["url"]), r["url"]) for r in a.db.most_visited(12)]
            if mv:
                self._section("Most visited", mv, removable="history")
        bm = [(r["title"], r["url"]) for r in a.db.bookmarks(12)]
        if bm:
            self._section("Bookmarks", bm, removable="bookmark")

    def _section(self, title, items, removable, indexes=None):
        self.box.append(label(title, "sofa-section-title"))
        if not items:
            self.box.append(label("Pin a page here with “Pin to home” in the menu.", "sofa-hint"))
            return
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_max_children_per_line(6)
        flow.set_min_children_per_line(3)
        flow.set_column_spacing(16)
        flow.set_row_spacing(16)
        flow.set_activate_on_single_click(True)
        for n, (title_, url) in enumerate(items):
            child = Gtk.FlowBoxChild()
            child.url, child.title, child.kind = url, title_, removable
            child.index = indexes[n] if indexes else None
            child.add_css_class("sofa-tile")
            child.add_css_class("card")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_valign(Gtk.Align.CENTER)
            box.append(site_icon(url, title=title_))
            box.append(label(title_ or pretty_host(host_of(url) or url), "sofa-tile-title", xalign=0.5))
            box.append(label(host_of(url) or url, "sofa-tile-host", xalign=0.5))
            if removable == "tab" and child.index == self.app.browser.current:
                child.add_css_class("sofa-current-tab")
                box.append(label("This tab", "sofa-tile-host", xalign=0.5))
            child.set_child(box)
            child.set_tooltip_text(url)
            right = Gtk.GestureClick(button=3)
            right.connect("pressed", lambda g, n, x, y, c=child: self._tile_menu(c))
            child.add_controller(right)
            flow.append(child)
        flow.connect("child-activated", lambda f, c: self.app.switch_tab(c.index) if c.kind == "tab"
                     else self.app.open_url(c.url))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._tile_key, flow)
        flow.add_controller(keys)
        self.box.append(flow)

    def _tile_key(self, _ctl, keyval, _code, _state, flow):
        name = Gdk.keyval_name(keyval) or ""
        if name in ("Menu", "XF86MenuKB", "Delete", "ContextMenu"):
            focus = self.get_root().get_focus()
            while focus is not None and not isinstance(focus, Gtk.FlowBoxChild):
                focus = focus.get_parent()
            if focus is not None:
                self._tile_menu(focus)
                return True
        return False

    def _tile_menu(self, child):
        what = {"tile": "Remove from Quick access", "history": "Remove from history",
                "bookmark": "Remove bookmark", "tab": "Close tab"}[child.kind]

        def done(choice):
            if choice == "remove":
                if child.kind == "tab":
                    self.app.close_tab(child.index)
                elif child.kind == "tile":
                    self.app.db.unpin(child.url)
                elif child.kind == "history":
                    for r in self.app.db.history(5000):
                        if host_of(r["url"]) == host_of(child.url):
                            self.app.db.remove_history(r["url"])
                else:
                    self.app.db.remove_bookmark(child.url)
                self.refresh()
            elif choice == "open":
                self.app.switch_tab(child.index) if child.kind == "tab" else self.app.open_url(child.url)

        self.app.choose(child.title or child.url, child.url, [("open", "Open"), ("remove", what)], done)


# The browse menu

ACTIONS = [
    ("home", "go-home-symbolic", "Home"),
    ("watch", "media-playback-start-symbolic", "Watch"),
    ("back", "go-previous-symbolic", "Back"),
    ("forward", "go-next-symbolic", "Forward"),
    ("reload", "view-refresh-symbolic", "Reload"),
    ("tabs", ["view-paged-symbolic", "view-grid-symbolic"], "Tabs"),
    ("new_tab", ["tab-new-symbolic", "list-add-symbolic"], "New tab"),
    ("find", "edit-find-symbolic", "Find in page"),
    ("bookmark", "non-starred-symbolic", "Bookmark"),
    ("pin", "list-add-symbolic", "Pin to home"),
    ("history", "document-open-recent-symbolic", "History"),
    ("bookmarks", "user-bookmarks-symbolic", "Bookmarks"),
    ("downloads", "folder-download-symbolic", "Downloads"),
    ("adblock", "security-high-symbolic", "Ad block"),
    ("cleanup", "edit-clear-all-symbolic", "Clean up"),
    ("zap", "edit-cut-symbolic", "Remove element"),
    ("allow_once", "window-new-symbolic", "Allow once"),
    ("auto_watch", "media-playlist-repeat-symbolic", "Watch by itself"),
    ("user_agent", "computer-symbolic", "Browse as"),
    ("scroll", "pan-down-symbolic", "Scroll mode"),
    ("pointer", "input-mouse-symbolic", "Pointer"),
    ("zoom_out", "zoom-out-symbolic", "Zoom out"),
    ("zoom_label", None, "100%"),
    ("zoom_in", "zoom-in-symbolic", "Zoom in"),
    ("fullscreen", "view-fullscreen-symbolic", "Full screen"),
    ("phone", ["phone-symbolic", "smartphone-symbolic"], "Phone"),
    ("settings", "emblem-system-symbolic", "Settings"),
    ("exit", ["application-exit-symbolic", "system-shutdown-symbolic"], "Exit"),
]


class Menu(Gtk.Box):
    """
    Opened with BACK, or the mouse at the top edge: the open tabs (when there are two or more), an address pill and
    rows of labelled buttons, across the top of the page. It remembers the last button used.
    """

    def __init__(self, on_address, on_action, on_tab, on_close_tab):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.add_css_class("sofa-sheet")
        self.set_valign(Gtk.Align.START)
        self.set_hexpand(True)
        self.on_tab, self.on_close_tab = on_tab, on_close_tab
        self.tab_strip = Gtk.Box(spacing=10, margin_start=8, margin_end=8, margin_top=4, margin_bottom=4)
        self.tab_scroll = Gtk.ScrolledWindow(propagate_natural_height=True)
        # Scrolls sideways, with no scroll bar: the arrows show where more tabs are.
        self.tab_scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.tab_scroll.set_child(self.tab_strip)
        self.tab_scroll.set_hexpand(True)
        # Arrows either side when the tabs don't all fit: each scrolls the strip most of its width.
        self.tab_left = self._tab_arrow("go-previous-symbolic", "Earlier tabs", -1)
        self.tab_right = self._tab_arrow("go-next-symbolic", "Later tabs", 1)
        self.tab_row = Gtk.Box(spacing=6)
        self.tab_row.append(self.tab_left)
        self.tab_row.append(self.tab_scroll)
        self.tab_row.append(self.tab_right)
        self.tab_row.set_visible(False)
        self.append(self.tab_row)
        adj = self.tab_scroll.get_hadjustment()
        adj.connect("changed", lambda _a: self._strip_resized())
        adj.connect("value-changed", lambda _a: self._update_arrows())
        self._update_arrows()
        self._tabs_key = None
        self._current_tab = None  # kept in view when the strip changes size (the arrows coming, say)
        self.address = Gtk.Button()
        ab = Gtk.Box(spacing=14)
        ab.append(Gtk.Image.new_from_icon_name("system-search-symbolic"))
        self.address_label = label("")
        self.address_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        ab.append(self.address_label)
        self.address.set_child(ab)
        self.address.add_css_class("sofa-address")
        self.address.set_halign(Gtk.Align.START)
        self.address.set_size_request(900, -1)
        self.address.connect("clicked", lambda _b: on_address())
        self.append(self.address)
        # The buttons wrap onto as many rows as the width needs, so none are hidden off to the side.
        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False, column_spacing=10,
                                row_spacing=10, max_children_per_line=64, margin_start=8, margin_end=8,
                                margin_top=6, margin_bottom=6)
        self.buttons = {}
        self.last = "home"
        for key, icon, text in ACTIONS:
            if icon is None:
                lb = label(text, xalign=0.5)
                lb.set_size_request(70, -1)
                self.zoom_label = lb
                self.grid.append(lb)
                continue
            b = icon_button(icon, text, lambda k=key: on_action(k))
            focus = Gtk.EventControllerFocus()
            focus.connect("enter", lambda _c, k=key: setattr(self, "last", k))
            b.add_controller(focus)
            self.buttons[key] = b
            self.grid.append(b)
        child = self.grid.get_first_child()
        while child is not None:
            child.set_focusable(False)
            child = child.get_next_sibling()
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._grid_key)
        self.grid.add_controller(keys)
        self.append(self.grid)

    def _grid_key(self, _ctl, keyval, _code, _state):
        """Arrow keys move between the buttons by where they sit, row to row, instead of by the FlowBox's own cursor."""
        name = (Gdk.keyval_name(keyval) or "").removeprefix("KP_")
        if name not in ("Up", "Down", "Left", "Right"):
            return False
        root = self.get_root()
        focus = root.get_focus() if root else None
        visible = [b for b in self.buttons.values() if b.get_visible() and b.get_parent().get_visible()
                   and b.get_mapped()]
        cur = next((b for b in visible if focus is b or (focus is not None and focus.is_ancestor(b))), None)
        if cur is None:
            return False

        def box(b):
            ok, r = b.compute_bounds(self.grid)
            return (r.get_x() + r.get_width() / 2, r.get_y() + r.get_height() / 2) if ok else (0, 0)

        pos = {b: box(b) for b in visible}
        rows = sorted({round(y) for _x, y in pos.values()})
        row_of = {b: rows.index(round(pos[b][1])) for b in visible}
        order = sorted(visible, key=lambda b: (row_of[b], pos[b][0]))
        r = row_of[cur]
        target = None
        if name in ("Left", "Right"):  # along the row, carrying on into the next or previous one at the ends
            i = order.index(cur) + (1 if name == "Right" else -1)
            target = order[i] if 0 <= i < len(order) else None
        else:
            want = r + (1 if name == "Down" else -1)
            if want < 0:
                self.address.grab_focus()
                return True
            same = [b for b in visible if row_of[b] == want]
            target = min(same, key=lambda b: abs(pos[b][0] - pos[cur][0])) if same else None
        if target is not None:
            target.grab_focus()
        return True

    def _set(self, key, icon=None, text=None, visible=True, enabled=True):
        b = self.buttons[key]
        b.set_visible(visible)
        if isinstance(b.get_parent(), Gtk.FlowBoxChild):
            b.get_parent().set_visible(visible)  # or the FlowBox leaves a gap where it was
        if icon:
            b.icon.set_from_icon_name(icon)
        if text:
            b.text.set_text(text)
        if enabled:
            b.remove_css_class("off")
        else:
            b.add_css_class("off")

    def _update_tabs(self, tabs):
        """[tabs]: (title, address, is current). A tab button switches to it; its × closes it."""
        key = tuple(tabs)
        if key == self._tabs_key:
            return
        self._tabs_key = key
        root = self.get_root()
        focus = root.get_focus() if root else None
        had = None  # (index, was the × focused) when the focus was in the strip
        for i, item in enumerate(self._tab_items()):
            if focus is not None and (focus is item or focus.is_ancestor(item)):
                had = (i, focus is item.close or focus.is_ancestor(item.close))
        while (c := self.tab_strip.get_first_child()) is not None:
            self.tab_strip.remove(c)
        for i, (title, uri, is_current) in enumerate(tabs):
            item = Gtk.Box(spacing=2)
            item.add_css_class("sofa-tab")
            if is_current:
                item.add_css_class("sofa-tab-current")
            b = Gtk.Button()
            b.add_css_class("flat")
            inner = Gtk.Box(spacing=10)
            path = favicon_path(host_of(uri))
            if uri and os.path.exists(path):
                img = Gtk.Image.new_from_file(path)
                img.set_pixel_size(24)
                inner.append(img)
            name = label(title or pretty_host(host_of(uri) or "") or "New tab")
            name.set_max_width_chars(18)
            name.set_width_chars(8)
            inner.append(name)
            b.set_child(inner)
            b.set_tooltip_text(uri or "New tab")
            b.connect("clicked", lambda _b, n=i: self.on_tab(n))
            close = Gtk.Button.new_from_icon_name("window-close-symbolic")
            close.add_css_class("flat")
            close.add_css_class("circular")
            close.set_valign(Gtk.Align.CENTER)
            close.set_tooltip_text("Close tab")
            close.connect("clicked", lambda _b, n=i: self.on_close_tab(n))
            item.append(b)
            item.append(close)
            item.tab, item.close = b, close
            self.tab_strip.append(item)
        self.tab_row.set_visible(len(tabs) > 1)
        current = next((i for i, t in enumerate(tabs) if t[2]), None)
        self._current_tab = current if len(tabs) > 1 else None
        if self._current_tab is not None:
            self.tab_strip.add_tick_callback(lambda *_a: self._show_tab(current))
        if had is not None and len(tabs) > 1:
            items = list(self._tab_items())
            item = items[min(had[0], len(items) - 1)]
            (item.close if had[1] else item.tab).grab_focus()
        elif had is not None:
            self.buttons["home"].grab_focus()

    def _tab_arrow(self, icon_name, tip, direction):
        b = Gtk.Button.new_from_icon_name(icon_name)
        b.add_css_class("sofa-tab-arrow")
        b.add_css_class("circular")
        b.set_valign(Gtk.Align.CENTER)
        b.set_tooltip_text(tip)
        b.connect("clicked", lambda _b: self._scroll_tabs(direction))
        return b

    def _scroll_tabs(self, direction):
        adj = self.tab_scroll.get_hadjustment()
        top = adj.get_upper() - adj.get_page_size()
        adj.set_value(min(max(adj.get_value() + direction * adj.get_page_size() * 0.8, 0), top))

    def _strip_resized(self):
        self._update_arrows()
        if self._current_tab is not None:
            self._show_tab(self._current_tab)

    def _update_arrows(self):
        """
        While the tabs overflow, an arrow on each side with more tabs. At an end its arrow goes, but keeps its place,
        so the tabs don't shift; if it had the focus, the tab at that end gets it.
        """
        adj = self.tab_scroll.get_hadjustment()
        top = adj.get_upper() - adj.get_page_size()
        items = list(self._tab_items())
        for b, more, edge in ((self.tab_left, adj.get_value() > 10, items[:1]),
                              (self.tab_right, adj.get_value() < top - 10, items[-1:])):
            b.set_visible(top > 1)
            had_focus = b.has_focus()
            b.set_opacity(1.0 if more else 0.0)
            b.set_can_target(more)
            b.set_can_focus(more)
            if had_focus and not more and edge:
                edge[0].tab.grab_focus()

    def _show_tab(self, index):
        """Scrolls the strip so tab [index] is in view, once it has its place (a tick callback: runs when shown)."""
        items = list(self._tab_items())
        if index >= len(items):
            return GLib.SOURCE_REMOVE
        item = items[index]
        if item.get_width() <= 0:
            return GLib.SOURCE_CONTINUE
        ok, bounds = item.compute_bounds(self.tab_strip)
        if not ok:
            return GLib.SOURCE_CONTINUE
        adj = self.tab_scroll.get_hadjustment()
        x, w, page = bounds.get_x(), bounds.get_width(), adj.get_page_size()
        if x < adj.get_value():
            adj.set_value(max(x - 8, 0))
        elif x + w > adj.get_value() + page:
            adj.set_value(min(x + w + 8 - page, adj.get_upper() - page))
        return GLib.SOURCE_REMOVE

    def _tab_items(self):
        c = self.tab_strip.get_first_child()
        while c is not None:
            yield c
            c = c.get_next_sibling()

    def update(self, s):
        self._update_tabs(s["tab_list"])
        self.address_label.set_text(s["address"] or "Search or type an address")
        self._set("back", enabled=s["can_back"])
        self._set("forward", enabled=s["can_forward"])
        self._set("reload", "process-stop-symbolic" if s["loading"] else "view-refresh-symbolic",
                  "Stop" if s["loading"] else "Reload")
        self._set("bookmark", "starred-symbolic" if s["bookmarked"] else "non-starred-symbolic",
                  "Bookmarked" if s["bookmarked"] else "Bookmark")
        self._set("pin", text="Pinned" if s["pinned"] else "Pin to home")
        self._set("tabs", text=f"Tabs ({s['tabs']})")
        self._set("new_tab", enabled=s["can_add_tab"])
        self._set("adblock", "security-high-symbolic" if s["adblock"] else "security-low-symbolic",
                  "Ad block: on" if s["adblock"] else "Ad block: off")
        self._set("allow_once", visible=s["pending"])
        self._set("cleanup", text="Clean up: auto" if s["autoclean"] else "Clean up")
        self._set("auto_watch", visible=not s["private"],
                  text="Watch by itself: on" if s["auto_watch"] else "Watch by itself: off")
        self._set("user_agent", "phone-symbolic" if s["ua"] in ("mobile", "ipad") else "computer-symbolic",
                  f"Browse as: {s['ua_label']}")
        self._set("pointer", "input-mouse-symbolic" if s["pointer"] else "input-keyboard-symbolic",
                  "Pointer: on" if s["pointer"] else "Pointer: off")
        self._set("fullscreen", text="Window" if s["fullscreen"] else "Full screen")
        self._set("phone", text="Phone: on" if s["phone"] else "Phone")
        self.zoom_label.set_text(f"{s['zoom']}%")

    def focus_first(self, pending):
        key = "allow_once" if pending else self.last
        b = self.buttons.get(key)
        if not b or not b.get_visible():
            b = self.buttons["home"]
        b.grab_focus()


# Search

class SearchPanel(Gtk.Box):
    """The address and search field, with history matches and (if turned on) the engine's suggestions."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.app = app
        self.add_css_class("sofa-overlay-panel")
        self.set_valign(Gtk.Align.START)
        self.set_halign(Gtk.Align.CENTER)
        self.set_margin_top(60)
        self.set_size_request(1000, -1)
        self.entry = Gtk.Entry()
        self.entry.add_css_class("sofa-search-entry")
        self.entry.set_input_purpose(Gtk.InputPurpose.URL)
        self.entry.connect("activate", lambda e: self.submit(e.get_text()))
        self.entry.connect("changed", lambda e: self._changed())
        self.append(self.entry)
        self.list = Gtk.ListBox()
        self.list.add_css_class("boxed-list")
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list.connect("row-activated", lambda lb, row: self.submit(row.value))
        self.list.set_visible(False)
        self.append(self.list)
        self._gen = 0

    def open(self, text):
        e = self.app.prefs.engine
        self.entry.set_placeholder_text(f"Search with {e.name} or type an address")
        self.entry.set_text(text or "")
        self._changed()
        self.entry.grab_focus()
        self.entry.select_region(0, -1)

    def submit(self, text):
        if text and text.strip():
            self.app.hide_search()
            self.app.open_text(text.strip())

    def _changed(self):
        text = self.entry.get_text().strip()
        self._gen += 1
        gen = self._gen
        items = []
        if text and not self.app.browser.private:
            t = text.lower()
            for r in self.app.db.history(2000):
                if t in r["url"].lower() or t in (r["title"] or "").lower():
                    items.append((r["title"] or r["url"], r["url"], "document-open-recent-symbolic"))
                if len(items) >= 4:
                    break
        self._show(items)
        eng = self.app.prefs.engine
        # Private browsing sends nothing as you type, even with suggestions on.
        if text and self.app.prefs.search_suggestions and eng.suggest and not self.app.browser.private:
            url = eng.suggest_url(text)

            def fetch():
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=4) as r:
                        data = json.loads(r.read(200000).decode("utf-8", "replace"))
                    sugg = [s for s in data[1] if isinstance(s, str)][:6]
                except Exception:  # noqa: BLE001 - suggestions are optional
                    return
                GLib.idle_add(lambda: gen == self._gen and self._show(
                    items + [(s, s, "system-search-symbolic") for s in sugg]) and False)

            threading.Thread(target=fetch, daemon=True).start()

    def _show(self, items):
        self.list.remove_all()
        for title, value, icon in items:
            row = Gtk.ListBoxRow()
            row.value = value
            b = Gtk.Box(spacing=14)
            b.add_css_class("sofa-suggestion")
            b.append(Gtk.Image.new_from_icon_name(icon))
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            v.append(label(title))
            if value != title:
                v.append(label(value, "sofa-row-sub"))
            b.append(v)
            row.set_child(b)
            self.list.append(row)
        self.list.set_visible(bool(items))
        return True


# Find in page

class FindBar(Gtk.Box):
    def __init__(self, app):
        super().__init__(spacing=12)
        self.app = app
        self.add_css_class("sofa-findbar")
        self.set_valign(Gtk.Align.START)
        self.set_halign(Gtk.Align.CENTER)
        self.entry = Gtk.SearchEntry()
        self.entry.set_size_request(500, -1)
        self.entry.connect("search-changed", lambda e: self._search())
        self.entry.connect("activate", lambda e: self._ctl().search_next())
        self.entry.connect("next-match", lambda e: self._ctl().search_next())
        self.entry.connect("previous-match", lambda e: self._ctl().search_previous())
        self.entry.connect("stop-search", lambda e: app.hide_find())
        self.append(self.entry)
        self.count = label("", "sofa-hint")
        self.count.set_size_request(120, -1)
        self.append(self.count)
        for icon, cb in (("go-up-symbolic", lambda: self._ctl().search_previous()),
                         ("go-down-symbolic", lambda: self._ctl().search_next()),
                         ("window-close-symbolic", app.hide_find)):
            b = Gtk.Button.new_from_icon_name(icon)
            b.connect("clicked", lambda _b, c=cb: c())
            self.append(b)
        self._view = None

    def _ctl(self):
        return self.app.browser.view.get_find_controller()

    def attach(self, view):
        if view is self._view:
            return
        self._view = view
        ctl = view.get_find_controller()
        ctl.connect("counted-matches", lambda c, n: self.count.set_text(f"{n} found"))
        ctl.connect("failed-to-find-text", lambda c: self.count.set_text("Not found"))

    def open(self):
        self.attach(self.app.browser.view)
        self.entry.grab_focus()
        self._search()

    def _search(self):
        text = self.entry.get_text()
        ctl = self._ctl()
        if not text:
            ctl.search_finish()
            self.count.set_text("")
            return
        from gi.repository import WebKit
        opts = WebKit.FindOptions.CASE_INSENSITIVE | WebKit.FindOptions.WRAP_AROUND
        ctl.count_matches(text, opts, 1000)
        ctl.search(text, opts, 1000)

    def close(self):
        if self._view:
            self._ctl().search_finish()


# List pages: history, bookmarks, downloads

class ListPage(Gtk.Box):
    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.app = app
        self.kind = None
        self.add_css_class("sofa-page")
        head = Gtk.Box(spacing=16)
        self.back = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        self.back.connect("clicked", lambda _b: app.go_back_from_page())
        head.append(self.back)
        self.title = label("", "sofa-page-title")
        self.title.set_hexpand(True)
        head.append(self.title)
        self.clear = Gtk.Button(label="Clear all")
        self.clear.add_css_class("destructive-action")
        self.clear.connect("clicked", lambda _b: self._clear())
        head.append(self.clear)
        self.folder = Gtk.Button(label="Open folder")
        self.folder.connect("clicked", lambda _b: app.open_downloads_folder())
        head.append(self.folder)
        self.append(head)
        sc = Gtk.ScrolledWindow()
        sc.set_vexpand(True)
        self.list = Gtk.ListBox()
        self.list.add_css_class("boxed-list")
        self.list.add_css_class("sofa-list")
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list.connect("row-activated", self._activated)
        sc.set_child(self.list)
        self.append(sc)
        self.empty = label("", "sofa-hint", xalign=0.5)
        self.append(self.empty)
        self._timer = 0

    def show(self, kind):
        self.kind = kind
        self.title.set_text({"history": "History", "bookmarks": "Bookmarks", "downloads": "Downloads"}[kind])
        self.folder.set_visible(kind == "downloads")
        self.refresh()
        first = self.list.get_row_at_index(0)
        (first or self.back).grab_focus()
        if kind == "downloads" and not self._timer:
            self._timer = GLib.timeout_add(500, self._tick)

    def _tick(self):
        if self.kind != "downloads" or not self.get_mapped():
            self._timer = 0
            return False
        for row in self._rows():
            if getattr(row, "download", None) is not None:
                self._update_download_row(row)
        return True

    def _rows(self):
        r = self.list.get_first_child()
        while r is not None:
            yield r
            r = r.get_next_sibling()

    def refresh(self):
        self.list.remove_all()
        db = self.app.db
        if self.kind == "history":
            rows = [(r["title"] or r["url"], r["url"], r["url"]) for r in db.history()]
        elif self.kind == "bookmarks":
            rows = [(r["title"] or r["url"], r["url"], r["url"]) for r in db.bookmarks()]
        else:
            rows = None
            for d in db.downloads():
                self.list.append(self._download_row(d))
        if rows is not None:
            for title, sub, key in rows:
                self.list.append(self._row(title, sub, key))
        n = len(list(self._rows()))
        self.empty.set_text("" if n else {"history": "No history yet.", "bookmarks": "No bookmarks yet.",
                                          "downloads": "No downloads yet."}[self.kind])
        self.empty.set_visible(not n)
        self.clear.set_visible(bool(n))

    def _row(self, title, sub, key):
        row = Gtk.ListBoxRow()
        row.key = key
        box = Gtk.Box(spacing=16)
        box.append(site_icon(key, 32))
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        v.set_hexpand(True)
        v.append(label(title, "sofa-row-title"))
        v.append(label(sub, "sofa-row-sub"))
        box.append(v)
        rm = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        rm.add_css_class("flat")
        rm.set_tooltip_text("Remove")
        rm.connect("clicked", lambda _b: self._remove(row))
        box.append(rm)
        row.set_child(box)
        return row

    def _download_row(self, d):
        row = Gtk.ListBoxRow()
        row.key = d["id"]
        row.path = d["path"]
        row.download = self.app.active_downloads.get(d["id"])
        box = Gtk.Box(spacing=16)
        box.append(Gtk.Image.new_from_icon_name("folder-download-symbolic"))
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        v.set_hexpand(True)
        v.append(label(os.path.basename(d["path"] or d["url"]), "sofa-row-title"))
        row.status = label("", "sofa-row-sub")
        v.append(row.status)
        row.bar = Gtk.ProgressBar()
        row.bar.add_css_class("sofa-progress")
        v.append(row.bar)
        box.append(v)
        row.state = d["state"]
        row.size = d["size"]
        row.cancel = Gtk.Button.new_from_icon_name("process-stop-symbolic")
        row.cancel.add_css_class("flat")
        row.cancel.set_tooltip_text("Cancel")
        row.cancel.connect("clicked", lambda _b: row.download and self.app.cancel_download(row.download))
        box.append(row.cancel)
        rm = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        rm.add_css_class("flat")
        rm.set_tooltip_text("Remove from list")
        rm.connect("clicked", lambda _b: self._remove(row))
        box.append(rm)
        row.set_child(box)
        self._update_download_row(row)
        return row

    def _update_download_row(self, row):
        dl = row.download
        if dl is not None and row.key in self.app.active_downloads:
            got = dl.get_received_data_length()
            resp = dl.get_response()
            total = resp.get_content_length() if resp else 0
            row.bar.set_fraction(dl.get_estimated_progress())
            row.status.set_text(f"{_size(got)} of {_size(total)}" if total else _size(got))
            row.bar.set_visible(True)
            row.cancel.set_visible(True)
        else:
            state = next((d["state"] for d in self.app.db.downloads() if d["id"] == row.key), row.state)
            size = next((d["size"] for d in self.app.db.downloads() if d["id"] == row.key), row.size)
            row.download = None
            row.status.set_text({"done": f"Done · {_size(size)}", "failed": "Failed", "cancelled": "Cancelled",
                                 "running": "Stopped"}.get(state, state))
            row.bar.set_visible(False)
            row.cancel.set_visible(False)

    def _activated(self, _lb, row):
        if self.kind == "downloads":
            if row.path and os.path.exists(row.path):
                self.app.open_file(row.path)
        else:
            self.app.open_url(row.key)

    def _remove(self, row):
        db = self.app.db
        if self.kind == "history":
            db.remove_history(row.key)
        elif self.kind == "bookmarks":
            db.remove_bookmark(row.key)
        else:
            if row.download:
                self.app.cancel_download(row.download)
            db.remove_download(row.key)
        self.list.remove(row)

    def _clear(self):
        what = {"history": "all history", "bookmarks": "all bookmarks", "downloads": "the finished downloads"}

        def done(choice):
            if choice != "clear":
                return
            if self.kind == "history":
                self.app.db.clear_history()
            elif self.kind == "bookmarks":
                for r in self.app.db.bookmarks():
                    self.app.db.remove_bookmark(r["url"])
            else:
                self.app.db.clear_downloads()
            self.refresh()

        self.app.choose(f"Clear {what[self.kind]}?", "Files you downloaded stay where they are."
                        if self.kind == "downloads" else "", [("cancel", "Cancel"), ("clear", "Clear")], done,
                        destructive="clear")


def _size(n):
    n = n or 0
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def alert(parent, heading, body, responses, on_done, destructive=None, extra=None, on_closed=None, default=None):
    """
    A dialog with a button per response; [on_done](response id). Escape or Back answers the first. Enter answers
    [default]: the destructive response's opposite, or else the last.
    """
    d = Adw.AlertDialog(heading=heading, body=body or "")
    for rid, text in responses:
        d.add_response(rid, text)
    if destructive:
        d.set_response_appearance(destructive, Adw.ResponseAppearance.DESTRUCTIVE)
    d.set_close_response(responses[0][0])
    d.set_default_response(default or (responses[-1][0] if not destructive else responses[0][0]))
    if extra is not None:
        d.set_extra_child(extra)
    d.connect("response", lambda _d, r: on_done(r))
    if on_closed:
        d.connect("closed", lambda _d: on_closed())
    d.present(parent)
    return d


def choose_list(parent, heading, body, items, on_closed=None, width=760):
    """
    A dialog with a large list of choices for a remote: [items] are (title, subtitle, callback), or None for a
    gap. Choosing one closes the dialog and runs its callback. Back or Escape closes it.
    """
    d = Adw.Dialog(title=heading)
    d.set_content_width(width)
    tv = Adw.ToolbarView()
    tv.add_top_bar(Adw.HeaderBar())
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=24, margin_end=24,
                  margin_bottom=24)
    if body:
        box.append(label(body, "sofa-hint", wrap=True))
    lb = Gtk.ListBox()
    lb.add_css_class("boxed-list")
    lb.set_selection_mode(Gtk.SelectionMode.NONE)
    chosen = []

    def activated(row):
        if not chosen:
            chosen.append(row.cb)
            d.force_close()

    for item in items:
        if item is None:
            continue
        title, sub, cb = item
        row = Adw.ActionRow(title=GLib.markup_escape_text(title), subtitle=GLib.markup_escape_text(sub or ""))
        row.set_activatable(True)
        row.cb = cb
        # "activated" comes from Enter and clicks on the row, and from a game pad's or the phone's OK too.
        row.connect("activated", activated)
        lb.append(row)
    sc = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=760,
                            hscrollbar_policy=Gtk.PolicyType.NEVER)
    sc.set_child(lb)
    box.append(sc)
    tv.set_content(box)
    d.set_child(tv)
    d.list = lb

    def closed(_d):
        if on_closed:
            on_closed()
        if chosen:
            chosen[0]()

    d.connect("closed", closed)
    d.present(parent)
    first = lb.get_row_at_index(0)
    if first is not None:
        GLib.idle_add(lambda: first.grab_focus() and False)
    return d


class QrCode(Gtk.DrawingArea):
    """A QR code from rows of '0' and '1', black on white with the quiet zone around it."""

    def __init__(self, size=360):
        super().__init__()
        self.rows = None
        self.set_size_request(size, size)
        self.set_draw_func(self._draw)

    def set_rows(self, rows):
        self.rows = rows
        self.queue_draw()

    def _draw(self, _a, cr, w, h):
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        if not self.rows:
            return
        n = len(self.rows) + 8
        cell = min(w, h) // n
        ox, oy = (w - cell * n) // 2 + 4 * cell, (h - cell * n) // 2 + 4 * cell
        cr.set_source_rgb(0, 0, 0)
        for y, row in enumerate(self.rows):
            for x, c in enumerate(row):
                if c == "1":
                    cr.rectangle(ox + x * cell, oy + y * cell, cell, cell)
        cr.fill()
