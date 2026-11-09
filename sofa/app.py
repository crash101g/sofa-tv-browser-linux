# SPDX-License-Identifier: GPL-3.0-only
"""Sofa TV Browser for Linux: the window, the remote's keys, Watch, and everything that ties the parts together."""

import os
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, WebKit  # noqa: E402

from . import paths, phone, player as player_mod, qr  # noqa: E402
from .adblock import AdBlocker  # noqa: E402
from .browser import MAX_TABS, Browser  # noqa: E402
from .cursors import BigCursors  # noqa: E402
from .db import Db, host_of, site_of  # noqa: E402
from .gamepad import GamePads  # noqa: E402
from .pointer import OK_KEYS, Pointer  # noqa: E402
from .prefs import UA_LABELS, Prefs  # noqa: E402
from .settings import Settings, Setup  # noqa: E402
from .ui import FindBar, Home, ListPage, Menu, QrCode, SearchPanel, alert, choose_list, label  # noqa: E402

APP_ID = "app.sofabrowser.linux"

BACK_KEYS = {"Escape", "XF86Back", "BackSpace"}
MENU_KEYS = {"Menu", "XF86MenuKB", "F1", "XF86Option"}
PLAY_KEYS = {"XF86AudioPlay", "XF86AudioPause", "XF86AudioMedia", "F2", "XF86Video"}
HOME_KEYS = {"XF86HomePage"}
SEARCH_KEYS = {"XF86Search", "F6", "XF86Explorer"}
DIRECTION_KEYS = {"Up", "Down", "Left", "Right", "KP_Up", "KP_Down", "KP_Left", "KP_Right"}

UA_CYCLE = ["desktop", "mobile", "tv", "webkit"]
AUTO_WATCH_SETTLE_S = 1.5
AUTO_WATCH_TRIES = 6
OWN_VIDEO_DEBOUNCE_S = 4.0
TOP_EDGE = 3             # the mouse this close to the top of the window opens the menu
PHONE_STEP = 40          # a phone arrow moves the pointer this far (at 1920 px wide)
PHONE_FOCUS_STEP = 70    # trackpad travel for one focus move on Sofa's own screens
PHONE_SCROLL_GAIN = 2.5
PHONE_DIRS = {"up": "Up", "down": "Down", "left": "Left", "right": "Right"}
EDIT_MOVES = {"up": "MoveUp", "down": "MoveDown", "left": "MoveLeft", "right": "MoveRight"}


class PhoneActions:
    """What the phone page asks for, run on the main loop (see phone.PhoneServer)."""

    def __init__(self, win):
        self.w = win

    def open_url(self, text):
        self.w.phone_open(text)

    def type_text(self, text):
        self.w.phone_type(text)

    def key(self, name):
        self.w.phone_key(name)

    def pointer(self, op, a, b):
        self.w.phone_pointer(op, a, b)

    def pointer_connected(self):
        self.w.toast("Phone trackpad connected")

    def pointer_disconnected(self):
        self.w.pointer.release()


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Sofa")
        self.add_css_class("sofa")
        self.set_default_size(1600, 900)
        self.prefs = Prefs()
        self.db = Db()
        self.db.trim_history(self.prefs.history_days)
        self.adblock = AdBlocker(self.prefs)
        self.cursors = BigCursors()
        self.cursors.set_size(self.prefs.mouse_size)
        self.browser = Browser(self.prefs, self.adblock, self.db, self)
        self.player = player_mod.Player(self.prefs)
        self.active_downloads = {}
        self.page_stack = []
        self.dialog = None
        self.web_fullscreen = False
        self.zap_next = False
        self._pad_acc = [0.0, 0.0]
        self.watching = False
        self.auto_watching = False
        self.candidates = []
        self.auto_watch_url = None
        self.auto_watch_tries = 0
        self.auto_watch_timer = 0
        self.last_own_video = 0.0
        self.real_mouse = None
        self.phone_tokens = phone.TokenStore()
        self.phone_certs = phone.CertStore()
        self.phone = phone.PhoneServer(self.phone_tokens, PhoneActions(self),
                                       lambda fn, *a: GLib.idle_add(lambda: (fn(*a), False)[1]),
                                       tls=lambda: None if self.prefs.phone_plain_http else self.phone_certs.context())

        self.css = Gtk.CssProvider()
        self.css.load_from_path(paths.resource("style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.scale_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.scale_css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.apply_theme()

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)
        self.root = Gtk.Overlay()
        self.toasts.set_child(self.root)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150)
        self.root.set_child(self.stack)

        # The page: web view, pointer, progress.
        self.web_box = Gtk.Overlay()
        self.pointer = Pointer(self.browser, self.on_hold_menu, self.on_scroll_mode)
        self.apply_pointer_speed()
        self.apply_mouse_size()
        self.web_box.add_overlay(self.pointer)
        self.progress = Gtk.ProgressBar(valign=Gtk.Align.START)
        self.progress.add_css_class("sofa-progress")
        self.progress.add_css_class("osd")
        self.progress.set_visible(False)
        self.web_box.add_overlay(self.progress)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._real_mouse_moved)
        self.web_box.add_controller(motion)
        self.web_stack = Gtk.Stack(hexpand=True, vexpand=True)
        self.web_box.set_child(self.web_stack)
        self._restore_tabs()

        self.home = Home(self)
        self.lists = ListPage(self)
        self.settings_page = Settings(self)
        self.setup = Setup(self)
        self.stack.add_named(self.setup, "setup")
        self.stack.add_named(self.home, "home")
        self.stack.add_named(self.web_box, "web")
        self.stack.add_named(self.lists, "list")
        self.stack.add_named(self.settings_page, "settings")

        self.menu = Menu(self.show_search_from_menu, self.on_menu_action, self.menu_tab, self.menu_close_tab)
        self.menu_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                          valign=Gtk.Align.START)
        self.menu_revealer.set_child(self.menu)
        self.root.add_overlay(self.menu_revealer)
        # The mouse at the very top of the window opens the menu; one it opened closes when the mouse moves back down
        # over the page, unless it was clicked.
        self.menu_by_mouse = False
        edge = Gtk.EventControllerMotion()
        edge.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        edge.connect("motion", self._mouse_at_edge)
        self.root.add_controller(edge)
        menu_click = Gtk.GestureClick(button=0)
        menu_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        menu_click.connect("pressed", lambda *_a: setattr(self, "menu_by_mouse", False))
        self.menu.add_controller(menu_click)
        self.search = SearchPanel(self)
        self.search.set_visible(False)
        self.root.add_overlay(self.search)
        self.find = FindBar(self)
        self.find.set_visible(False)
        self.root.add_overlay(self.find)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key_pressed)
        keys.connect("key-released", self._key_released)
        self.add_controller(keys)
        self.connect("close-request", self._close_request)
        self.connect("map", lambda _w: self._phone_sync())
        if self.find_property("suspended") is not None:
            self.connect("notify::suspended", lambda *_a: self._phone_sync())

        self.gamepads = GamePads(self._pad_action, self._pad_sticks,
                                 lambda name: self.toast(f"Game pad connected: {name}"))
        self.adblock.load()

        self.cursors.track_tree(self)
        if self.prefs.start_fullscreen:
            self.fullscreen()
        if not self.prefs.setup_done:
            self._show("setup")
            GLib.idle_add(lambda: self.setup.focus() and False)
        else:
            self.show_home()

    # Theme and size
    def apply_theme(self):
        sm = Adw.StyleManager.get_default()
        sm.set_color_scheme({"dark": Adw.ColorScheme.FORCE_DARK, "light": Adw.ColorScheme.FORCE_LIGHT}
                            .get(self.prefs.theme, Adw.ColorScheme.DEFAULT))
        pt = 11 * float(self.prefs.ui_scale)
        self.scale_css.load_from_string(f"window.sofa, window.sofa popover, window.sofa dialog-host {{ font-size: {pt:.1f}pt; }}")

    def apply_mouse_size(self):
        """The mouse pointer and the remote's pointer dot grow together."""
        self.cursors.set_size(self.prefs.mouse_size)
        self.pointer.size_factor = {0: 1.0, 48: 1.6, 64: 2.0, 96: 2.6}.get(self.prefs.mouse_size, 1.6)
        self.pointer.queue_draw()

    def apply_pointer_speed(self):
        self.pointer.speed_level = self.prefs.cursor_speed

    # Pages
    def _show(self, name):
        cur = self.stack.get_visible_child_name()
        if cur and cur != name and cur != "setup":
            self.page_stack = [p for p in self.page_stack if p != name] + [cur]
        self.stack.set_visible_child_name(name)
        self.close_menu()
        if name == "web":
            if self.browser.view:
                self.browser.view.grab_focus()
        else:
            self.pointer.reset()
            self.stop_auto_watch()

    @property
    def page(self):
        return self.stack.get_visible_child_name()

    def show_home(self):
        self.home.refresh()
        self._show("home")
        self.page_stack = []
        GLib.idle_add(lambda: self.home.first_focus and self.home.first_focus.grab_focus() and False)

    def back_to_page(self):
        if self.browser.tab and self.browser.tab.uri:
            self.browser.ensure_loaded(self.browser.tab)
            self._show("web")

    def show_list(self, kind):
        self._show("list")
        self.lists.show(kind)

    def show_settings(self):
        self._show("settings")
        self.settings_page.show()

    def go_back_from_page(self):
        has_page = bool(self.browser.tab and self.browser.tab.uri)
        while self.page_stack:
            prev = self.page_stack.pop()
            if prev == "web" and not has_page:
                continue
            if prev == "home":
                self.home.refresh()
            self.stack.set_visible_child_name(prev)
            if prev == "web":
                self.browser.ensure_loaded(self.browser.tab)
                self.browser.view.grab_focus()
            elif prev == "home":
                GLib.idle_add(lambda: self.home.first_focus.grab_focus() and False)
            return
        if self.page != "home":
            self.show_home()
        elif has_page:
            self.back_to_page()

    # Opening pages
    def open_url(self, url):
        self.browser.load(url)
        self.web_stack.set_visible_child(self.browser.view)
        self._show("web")

    def open_text(self, text):
        self.open_url(text)

    def show_search(self, text=""):
        self.close_menu()
        self.search.set_visible(True)
        self.search.open(text)

    def show_search_from_menu(self):
        self.show_search(self.browser.view.get_uri() if self.browser.view and self.page == "web" else "")

    def hide_search(self):
        self.search.set_visible(False)
        if self.page == "web" and self.browser.view:
            self.browser.view.grab_focus()
        elif self.page == "home" and self.home.first_focus:
            self.home.first_focus.grab_focus()

    def show_find(self):
        self.close_menu()
        self.find.set_visible(True)
        self.find.open()

    def hide_find(self):
        self.find.close()
        self.find.set_visible(False)
        if self.browser.view:
            self.browser.view.grab_focus()

    # Tabs
    @property
    def editing(self):
        return bool(self.browser.tab and self.browser.tab.editing)

    @editing.setter
    def editing(self, on):
        if self.browser.tab:
            self.browser.tab.editing = on

    @property
    def filled(self):
        return bool(self.browser.tab and self.browser.tab.filled)

    @filled.setter
    def filled(self, on):
        if self.browser.tab:
            self.browser.tab.filled = on

    @property
    def page_title(self):
        return self.browser.tab.title if self.browser.tab else ""

    def _restore_tabs(self):
        p = self.prefs
        saved = p.tabs if p.restore_tabs and not p.clear_on_exit else []
        for item in saved:
            if self.browser.tab_room():
                break  # six, or memory is short: the rest aren't reopened
            url = item.get("url") if isinstance(item, dict) else None
            if not url or not url.startswith(("http://", "https://", "file:")):
                continue
            tab = self.browser.make_tab(False)
            tab.restore_url, tab.restore_title = url, item.get("title") or ""
            self._add_view(tab)
            self.browser.tabs.append(tab)
        if not self.browser.tabs:
            tab = self.browser.make_tab(False)
            self._add_view(tab)
            self.browser.tabs.append(tab)
        self.browser.current = min(max(int(p.current_tab or 0), 0), len(self.browser.tabs) - 1)
        self.web_stack.set_visible_child(self.browser.view)

    def _add_view(self, tab):
        self.web_stack.add_child(tab.view)

    @staticmethod
    def _discard(tab):
        """A closed tab, or a pop-up that wasn't let in: its page goes at once, sound and all."""
        tab.view.stop_loading()
        tab.view.load_uri("about:blank")

    def _save_tabs(self):
        b = self.browser
        if b.private or not self.prefs.restore_tabs:
            return
        tabs = [{"url": t.uri, "title": t.title} for t in b.tabs if t.uri.startswith(("http://", "https://", "file:"))]
        self.prefs.tabs = tabs
        self.prefs.current_tab = b.current if tabs else 0

    def switch_tab(self, i, show_page=True):
        b = self.browser
        if not 0 <= i < len(b.tabs):
            return
        old = b.tab
        if old is not None and old is not b.tabs[i]:
            b.call("pauseAll", tab=old)  # a tab out of sight stays quiet
            if old.filled:
                b.call("unfill", tab=old)
                old.filled = False
        self.zap_next = False
        b.current = i
        tab = b.tab
        self.web_stack.set_visible_child(tab.view)
        self.progress.set_visible(tab.view.is_loading())
        tab.view.set_is_muted(False)  # opened in the background, muted until now
        self.on_title(tab, tab.title)
        if show_page and tab.uri:
            b.ensure_loaded(tab)
            if self.page != "web":
                self._show("web")
            elif not self.menu_open:
                tab.view.grab_focus()
        elif show_page:
            self.show_home()
        self._save_tabs()
        self._update_menu()

    def new_tab(self, url=None, after=None):
        """
        A new tab showing [url] after the current one (or after [after]), or an empty one at the end, showing Home.
        False at the limit.
        """
        b = self.browser
        why = b.tab_room()
        if why:
            self.toast(f"{why}. Close one in Tabs, in the menu", timeout=4)
            return False
        tab = b.make_tab(b.private)
        self._add_view(tab)
        at = (b.tabs.index(after) if after in b.tabs else b.current) + 1 if url or after else len(b.tabs)
        b.tabs.insert(at, tab)
        if url:
            self.switch_tab(at, show_page=False)
            self.open_url(url)
        else:
            self.switch_tab(at)
            self.toast("New tab")
        return True

    def open_in_background(self, opener, url):
        """A right-clicked link: a new tab after its page (and after the tabs it opened before), not shown yet."""
        b = self.browser
        why = b.tab_room()
        if why:
            self.toast(f"{why}. Close one in the menu", timeout=4)
            return
        tab = b.make_tab(b.private)
        tab.view.set_is_muted(True)  # until it is shown
        tab.opened_by = opener
        self._add_view(tab)
        at = (b.tabs.index(opener) if opener in b.tabs else b.current) + 1
        while at < len(b.tabs) and b.tabs[at].opened_by is opener:
            at += 1
        b.tabs.insert(at, tab)
        if at <= b.current:
            b.current += 1
        b.load(url, tab=tab)
        self._save_tabs()
        self._update_menu()
        self.toast(f"Opened in a new tab · {len(b.tabs)} tabs open", timeout=2)

    def menu_tab(self, i):
        self.switch_tab(i)
        self.close_menu()

    def menu_close_tab(self, i):
        self.close_tab(i)
        if self.menu_open and len(self.browser.tabs) > 1:
            self._update_menu()

    def close_tab(self, i):
        b = self.browser
        if not 0 <= i < len(b.tabs):
            return
        tab = b.tabs.pop(i)
        was_current = i == b.current
        if i < b.current or (was_current and b.current >= len(b.tabs)):
            b.current = max(b.current - 1, 0)
        self.web_stack.remove(tab.view)
        self._discard(tab)
        if not b.tabs:
            fresh = b.make_tab(b.private)
            self._add_view(fresh)
            b.tabs.append(fresh)
            b.current = 0
            self.switch_tab(0, show_page=False)
            self.show_home()
        elif was_current:
            self.switch_tab(b.current, show_page=self.page == "web")
            if self.page == "home":
                self.home.refresh()
        else:
            self._save_tabs()
            if self.page == "home":
                self.home.refresh()
        self._update_menu()

    def next_tab(self, step=1):
        b = self.browser
        if len(b.tabs) > 1:
            self.switch_tab((b.current + step) % len(b.tabs))

    def tabs_dialog(self):
        b = self.browser
        items = []
        for i, t in enumerate(b.tabs):
            name = t.title or (host_of(t.uri) or "Home")
            sub = ("This tab · " if i == b.current else "") + (t.uri or "New tab")
            items.append((name, sub, lambda i=i: self.switch_tab(i)))
        if b.can_open_tab():
            items.append(("New tab", "", lambda: self.new_tab()))
        items.append(("Close this tab", b.tab.title or b.tab.uri or "", lambda: self.close_tab(b.current)))
        if len(b.tabs) > 1:
            items.append(("Close the other tabs", "", self.close_other_tabs))
        self.pick(f"Tabs ({len(b.tabs)})" if self.prefs.unlimited_tabs else f"Tabs ({len(b.tabs)} of {MAX_TABS})", "",
                  items)

    def close_other_tabs(self):
        keep = self.browser.tab
        for t in list(self.browser.tabs):
            if t is not keep:
                self.close_tab(self.browser.tabs.index(t))

    def rebuild_view(self):
        self.browser.refresh_content()
        if self.browser.view and self.browser.view.get_uri():
            self.browser.view.reload()

    def toggle_private(self):
        b = self.browser
        if b.private:
            for t in b.tabs:
                self.web_stack.remove(t.view)
                self._discard(t)
            b.tabs, b.current = b.saved
            b.saved = None
            b.private = False
            b.end_private()
            self.switch_tab(b.current, show_page=False)
            self.toast("Private browsing ended: its cookies and site data are gone")
        else:
            for t in b.tabs:
                b.call("pauseAll", tab=t)
            b.saved = (b.tabs, b.current)
            b.tabs, b.current, b.private = [], 0, True
            tab = b.make_tab(True)
            self._add_view(tab)
            b.tabs.append(tab)
            self.switch_tab(0, show_page=False)
            self.toast("Private browsing: nothing is kept")
        self.show_home()

    # Browser callbacks: the tab first; most only matter for the tab on screen.
    def _is_current(self, tab):
        return tab is self.browser.tab

    def on_title(self, tab, title):
        if self._is_current(tab):
            self.set_title(f"{title} — Sofa" if title else "Sofa")
        if self.menu_open:
            self._update_menu()  # the tab strip

    def on_uri(self, tab, uri):
        if self._is_current(tab):
            self._update_menu()

    def on_progress(self, tab, p):
        if self._is_current(tab):
            self.progress.set_fraction(p)

    def on_load_changed(self, tab, event):
        current = self._is_current(tab)
        if event == WebKit.LoadEvent.STARTED and current:
            self.progress.set_visible(True)
            self.stop_auto_watch()
            if self.zap_next:
                self.zap_next = False
        elif event == WebKit.LoadEvent.FINISHED:
            if current:
                self.progress.set_visible(False)
                self.maybe_auto_watch()
            self._save_tabs()
            site = site_of(host_of(tab.view.get_uri()))
            if site and self.prefs.in_set("autoclean_sites", site) and self.browser.adblock_on_for(tab.view.get_uri()):
                self.cleanup(tab, quiet=True)
                GLib.timeout_add(3000, lambda: (tab in self.browser.tabs and self.cleanup(tab, quiet=True), False)[1])
        if current:
            self._update_menu()

    def on_favicon(self, tab, host):
        pass

    def on_fullscreen(self, tab, on):
        self.web_fullscreen = on
        if on:
            self.was_fullscreen = self.is_fullscreen()
            self.fullscreen()
            self.close_menu()
        elif not getattr(self, "was_fullscreen", True):
            self.unfullscreen()

    def on_crash(self, tab, reason):
        if self._is_current(tab):
            self.toast("The page stopped working. Press Back and Reload to try again.")

    def on_editing(self, tab, editable):
        if editable and self._is_current(tab):
            self.pointer.hide_now()

    def on_blocked(self, tab, url):
        if self._is_current(tab):
            self.toast("Pop-up blocked. “Allow once” is in the menu.", timeout=3)

    def on_tab_limit(self, tab, why):
        self.toast(f"Opened here: {why}", timeout=4)

    def on_close_request(self, tab):
        # window.close() from a page: only a tab it opened itself has one to close.
        if tab in self.browser.tabs:
            self.close_tab(self.browser.tabs.index(tab))

    def on_popup(self, opener, new, uri, ask):
        """A window the page opened after a press: a new tab after its opener, or a question first (see Browser)."""
        b = self.browser

        def add():
            if opener not in b.tabs or b.tab_room():
                self._discard(new)
                if uri and uri != "about:blank":
                    opener.view.load_uri(uri)
                return
            self._add_view(new)
            at = b.tabs.index(opener) + 1
            b.tabs.insert(at, new)
            self.switch_tab(at, show_page=False)
            self._show("web")  # its page may not have an address yet

        if not ask:
            add()
            return
        host = host_of(uri) or uri or "a new window"
        def here():
            self._discard(new)
            if uri:
                opener.view.load_uri(uri)

        d = self.pick(f"This page wants to open {host}", "Pages often open ad windows when you click anywhere on them.", [
            ("Open it in a new tab", "", add),
            ("Open it here", "", here),
            ("Block it", "", lambda: self.toast("Pop-up blocked")),
        ])
        # Block it, or the dialog closed without an answer: the window never shows.
        d.connect("closed", lambda _d: new not in b.tabs and self._discard(new))

    def on_script_dialog(self, tab, dialog):
        """A page's alert, confirm or prompt, in Sofa's own dialog so the remote and the phone can answer it."""
        from gi.repository import WebKit as W
        kind = dialog.get_dialog_type()
        if not self._is_current(tab) or self.dialog is not None:
            if kind == W.ScriptDialogType.CONFIRM:
                dialog.confirm_set_confirmed(False)
            return False  # dismissed
        host = host_of(tab.uri) or "This page"
        entry = None
        if kind == W.ScriptDialogType.PROMPT:
            entry = Gtk.Entry(text=dialog.prompt_get_default_text() or "")
            entry.connect("activate", lambda _e: (result.append("ok"), d.force_close()))
        answers = [("ok", "OK")] if kind == W.ScriptDialogType.ALERT else [("cancel", "Cancel"), ("ok", "OK")]
        result = []

        def done(r):
            result.append(r)

        def closed():
            ok = bool(result) and result[0] == "ok"
            if kind == W.ScriptDialogType.CONFIRM:
                dialog.confirm_set_confirmed(ok)
            elif kind == W.ScriptDialogType.PROMPT:
                dialog.prompt_set_text(entry.get_text() if ok else None)
            dialog.close()
            if self.dialog is d:
                self.dialog = None
            if self.page == "web" and self.browser.view:
                self.browser.view.grab_focus()

        d = alert(self, f"{host} says", dialog.get_message() or "", answers, done, extra=entry, on_closed=closed)
        d.script_dialog = dialog  # keeps it until it is answered
        self.dialog = d
        if entry is not None:
            GLib.idle_add(lambda: entry.grab_focus() and False)
        return True

    def ask_external(self, tab, uri):
        """A page's link to another app on this computer (mailto:, magnet:, steam:, …): it opens only if the user says so."""
        if tab is None or not self._is_current(tab):
            return
        scheme = uri.split(":", 1)[0].lower()
        app = Gio.AppInfo.get_default_for_uri_scheme(scheme)
        if app is None:
            self.toast(f"No app on this computer opens {scheme}: links")
            return
        name = app.get_display_name()
        shown = uri if len(uri) <= 160 else uri[:157] + "…"
        self.choose(f"Open {name}?", f"{host_of(tab.uri) or 'This page'} wants to open a {scheme}: link in {name}:\n\n"
                    f"{shown}", [("cancel", "Cancel"), ("open", f"Open {name}")],
                    lambda r: r == "open" and Gio.AppInfo.launch_default_for_uri_async(uri, None, None, None, None),
                    destructive="open")

    def on_page_blocked(self, tab, host, uri):
        """A filter list blocked the page itself: it may be there by mistake, so offer to open it anyway."""
        if not self._is_current(tab):
            return

        def open_anyway(always):
            if always:
                self.prefs.set_in("adblock_off_sites", host, True)
                self.toast(f"Ad blocking off on {host}")
            else:
                self.browser.allowed_for_now.add(host)
            self.browser.refresh_content(tab, uri)
            tab.view.load_uri(uri)

        self.pick(f"Sofa blocked {host}", "It is on one of your ad and tracker lists. Sites are sometimes on them "
                  "by mistake. Opening it turns ad blocking off on this site.", [
                      ("Keep it blocked", "", lambda: None),
                      ("Open it this time", "Until Sofa closes", lambda: open_anyway(False)),
                      (f"Always allow {host}", "Turn ad blocking off on this site. The menu turns it back on",
                       lambda: open_anyway(True)),
                  ])

    def ask_tls(self, tab, host, go_on):
        if not self._is_current(tab):
            return
        self.choose(f"The connection to {host} is not secure",
                    "Its security certificate is not valid. Someone may be trying to read or change what you see.",
                    [("back", "Go back"), ("go", "Go on anyway")], lambda r: r == "go" and go_on(),
                    destructive="go")

    def on_select(self, tab, options, selected):
        if not self._is_current(tab):
            return
        lb = Gtk.ListBox()
        lb.add_css_class("boxed-list")
        lb.set_selection_mode(Gtk.SelectionMode.NONE)
        for o in options:
            row = Gtk.ListBoxRow()
            row.index = o["i"]
            row.set_sensitive(not o.get("disabled"))
            box = Gtk.Box(spacing=12)
            box.append(Gtk.Image.new_from_icon_name("object-select-symbolic" if o["i"] == selected else ""))
            box.append(Gtk.Label(label=o["label"], xalign=0))
            row.set_child(box)
            lb.append(row)
        sc = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=600)
        sc.set_child(lb)
        d = self.choose("Choose", "", [("cancel", "Cancel")], lambda r: None, extra=sc)

        def picked(_lb, row):
            self.browser.call("chooseAll", row.index, tab=tab)
            d.force_close()

        lb.connect("row-activated", picked)
        GLib.idle_add(lambda: (lb.get_row_at_index(max(selected, 0)) or lb).grab_focus() and False)

    def on_download(self, download):
        def destination(dl, suggested):
            """A download a page started is saved only if the user says so; one chosen in Sofa's menu just is."""
            name = os.path.basename(suggested or "download") or "download"
            if self.browser.take_own_download(dl.get_request().get_uri()):
                save(dl, name)
                return True
            view = dl.get_web_view()
            host = host_of(view.get_uri()) if view is not None else None
            answered = []

            def done(r):
                # The first answer counts. Closing the dialog answers again (Cancel), and closing it without an
                # answer (another dialog, the phone opening a page) is a Cancel too.
                if answered:
                    return
                answered.append(r)
                if r == "save":
                    save(dl, name)
                else:
                    self.cancel_download(dl)

            def ask():
                d = self.choose(f"Download {name}?", f"{host or 'A page'} wants to save {name} in your Downloads "
                                "folder.", [("cancel", "Cancel"), ("save", "Save")], done, default="cancel")
                d.connect("closed", lambda _d: done("cancel"))
                return False

            GLib.idle_add(ask)
            return True  # the destination comes later, from the answer

        def save(dl, name):
            folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.expanduser("~/Downloads")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, name)
            base, ext = os.path.splitext(path)
            n = 1
            while os.path.exists(path):
                path = f"{base} ({n}){ext}"
                n += 1
            dl.set_destination(path)
            dl.sofa_id = self.db.add_download(dl.get_request().get_uri(), path)
            self.active_downloads[dl.sofa_id] = dl
            self.toast(f"Downloading {os.path.basename(path)}")

        def failed(dl, error):
            i = getattr(dl, "sofa_id", None)
            if i is not None:
                cancelled = error.matches(WebKit.DownloadError.quark(), WebKit.DownloadError.CANCELLED_BY_USER)
                self.db.update_download(i, state="cancelled" if cancelled else "failed")
                dl.sofa_failed = True

        def finished(dl):
            dl.sofa_ended = True
            i = getattr(dl, "sofa_id", None)
            if i is None:
                return
            self.active_downloads.pop(i, None)
            if getattr(dl, "sofa_failed", False):
                return
            self.db.update_download(i, state="done", size=dl.get_received_data_length())
            path = dl.get_destination()
            t = Adw.Toast(title=f"Downloaded {os.path.basename(path or '')}", button_label="Open", timeout=5)
            t.connect("button-clicked", lambda _t: self.open_file(path))
            self.toasts.add_toast(t)

        download.connect("decide-destination", destination)
        download.connect("failed", failed)
        download.connect("finished", finished)

    def cancel_download(self, dl):
        """Stops a download, once, and not after it ended: WebKitGTK crashes on a second cancel or a late one."""
        def cancel():
            if not getattr(dl, "sofa_ended", False):
                dl.sofa_ended = True
                dl.cancel()
            return False

        GLib.idle_add(cancel)

    def open_file(self, path):
        Gio.AppInfo.launch_default_for_uri_async(Gio.File.new_for_path(path).get_uri(), None, None, None, None)

    def open_downloads_folder(self):
        folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.expanduser("~/Downloads")
        self.open_file(folder)

    # The menu
    def _mouse_at_edge(self, _ctl, _x, y):
        if self.page != "web" or self.dialog is not None or self.search.get_visible() or self.web_fullscreen:
            return
        if not self.menu_open:
            if y <= TOP_EDGE:
                self.open_menu(by_mouse=True)
        elif self.menu_by_mouse and y > self.menu_revealer.get_height() + 24:
            self.close_menu()

    def open_menu(self, by_mouse=False):
        if self.page != "web":
            return
        self.menu_by_mouse = by_mouse
        self.zap_next = False
        self.stop_auto_watch()
        self.pointer.reset()
        self._update_menu()
        self.menu_revealer.set_reveal_child(True)
        self.menu.focus_first(self.browser.tab.has_pending())

    def close_menu(self):
        if self.menu_revealer.get_reveal_child():
            self.menu_revealer.set_reveal_child(False)
            if self.page == "web" and self.browser.view:
                self.browser.view.grab_focus()

    @property
    def menu_open(self):
        return self.menu_revealer.get_reveal_child()

    def _update_menu(self):
        v = self.browser.view
        if v is None:
            return
        uri = self.browser.tab.uri
        host = host_of(uri)
        self.menu.update({
            "tab_list": [(t.title, t.uri, i == self.browser.current) for i, t in enumerate(self.browser.tabs)],
            "tabs": len(self.browser.tabs), "can_add_tab": self.browser.can_open_tab(), "phone": self.phone.running,
            "autoclean": self.prefs.in_set("autoclean_sites", site_of(host)),
            "address": uri, "can_back": v.can_go_back(), "can_forward": v.can_go_forward(),
            "loading": v.is_loading(), "bookmarked": bool(uri) and self.db.is_bookmarked(uri),
            "pinned": bool(uri) and self.db.is_pinned(uri), "adblock": self.browser.adblock_on_here(),
            "pending": self.browser.tab.has_pending(), "private": self.browser.private,
            "auto_watch": self.prefs.auto_watch == "always" or (
                self.prefs.auto_watch == "sites" and self.prefs.in_set("auto_watch_sites", host)),
            "ua": self.prefs.user_agent, "ua_label": UA_LABELS.get(self.prefs.user_agent, "Desktop"),
            "pointer": not self.prefs.in_set("pointer_off_sites", host),
            "zoom": round(v.get_zoom_level() * 100), "fullscreen": self.is_fullscreen(),
        })

    def on_menu_action(self, key):
        v = self.browser.view
        uri = v.get_uri() or ""
        host = host_of(uri)
        p = self.prefs
        keep_open = True
        if key == "home":
            self.show_home()
            return
        elif key == "watch":
            self.close_menu()
            self.watch()
            return
        elif key == "back" and v.can_go_back():
            v.go_back()
        elif key == "forward" and v.can_go_forward():
            v.go_forward()
        elif key == "reload":
            v.stop_loading() if v.is_loading() else v.reload()
        elif key == "find":
            self.show_find()
            return
        elif key == "bookmark" and uri:
            on = self.db.toggle_bookmark(uri, self.page_title)
            self.toast("Bookmarked" if on else "Bookmark removed")
        elif key == "pin" and uri:
            if self.db.is_pinned(uri):
                self.db.unpin(uri)
                self.toast("Removed from Home")
            else:
                self.db.pin(uri, self.page_title)
                self.toast("Pinned to Home")
        elif key in ("history", "bookmarks", "downloads"):
            self.show_list(key)
            return
        elif key == "tabs":
            self.close_menu()
            self.tabs_dialog()
            return
        elif key == "new_tab":
            self.close_menu()
            self.new_tab()
            return
        elif key == "adblock":
            self.close_menu()
            self.adblock_dialog()
            return
        elif key == "cleanup":
            self.close_menu()
            self.cleanup()
            return
        elif key == "zap":
            self.close_menu()
            self.start_zap()
            return
        elif key == "phone":
            self.close_menu()
            self.show_pairing()
            return
        elif key == "allow_once":
            url = self.browser.tab.take_pending()
            if url:
                self.browser.user_input()
                if not self.new_tab(url):
                    v.load_uri(url)
            keep_open = False
        elif key == "auto_watch" and host and not self.browser.private:
            if p.auto_watch == "always":
                self.toast("Watch by itself runs on every site (Settings → Video)")
            else:
                on = not p.in_set("auto_watch_sites", host)
                p.set_in("auto_watch_sites", host, on)
                if on and p.auto_watch == "off":
                    p.auto_watch = "sites"
                self.toast(f"Watch by itself on {host}" if on else f"Watch by itself off on {host}")
        elif key == "user_agent":
            cur = p.user_agent if p.user_agent in UA_CYCLE else "webkit"
            p.user_agent = UA_CYCLE[(UA_CYCLE.index(cur) + 1) % len(UA_CYCLE)]
            self.browser.reconfigure()
            v.reload()
        elif key == "scroll":
            self.close_menu()
            self.pointer.set_scroll_mode(True)
            return
        elif key == "pointer" and host:
            off = not p.in_set("pointer_off_sites", host)
            p.set_in("pointer_off_sites", host, off)
            self.toast("Arrow keys go to the page on this site" if off else "Pointer on")
            keep_open = False
        elif key in ("zoom_in", "zoom_out"):
            steps = [0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
            z = v.get_zoom_level()
            if key == "zoom_in":
                z = next((s for s in steps if s > z + 0.01), steps[-1])
            else:
                z = next((s for s in reversed(steps) if s < z - 0.01), steps[0])
            v.set_zoom_level(z)
        elif key == "fullscreen":
            self.unfullscreen() if self.is_fullscreen() else self.fullscreen()
        elif key == "settings":
            self.show_settings()
            return
        elif key == "exit":
            self.quit()  # the same as Ctrl+Q: open tabs are saved for next time
            return
        if keep_open:
            GLib.timeout_add(120, lambda: self._update_menu() and False)
        else:
            self.close_menu()

    # Ad blocking, clean up and remove element
    def toggle_site_adblock(self):
        tab = self.browser.tab
        uri = tab.uri
        host = host_of(uri)
        if not host:
            return
        if host in self.browser.allowed_for_now:  # opened anyway from the blocked page: turn blocking back on
            self.browser.allowed_for_now.discard(host)
            off = False
        else:
            off = not self.prefs.in_set("adblock_off_sites", host)
        self.prefs.set_in("adblock_off_sites", host, off)
        self.toast(f"Ad blocking off on {host}" if off else f"Ad blocking on for {host}")
        self.browser.refresh_content(tab, uri)
        tab.view.load_uri(uri)  # not reload(): that would show "Sofa blocked this page" again

    def adblock_dialog(self):
        p, ab, b = self.prefs, self.adblock, self.browser
        host = b.host
        site = site_of(host)
        on_here = b.adblock_on_here()
        state = "Off" if not p.adblock else "On" if on_here else "Off for this site"
        items = []
        if host and p.adblock:
            items.append((f"Turn ad blocking {'off' if on_here else 'on'} for {host}",
                          "Blocking, hiding and removed elements, for this site only", self.toggle_site_adblock))
        if b.tab.uri:
            items.append(("Clean up this page", "Removes overlays, cookie walls and sticky ad bars, and lets the page "
                                                "scroll again", self.cleanup))
        if site:
            auto = p.in_set("autoclean_sites", site)
            items.append((f"Stop cleaning up {site} by itself" if auto else f"Clean up {site} by itself",
                          "Every time one of its pages loads", lambda: self.set_autoclean(site, not auto)))
            items.append(("Remove an element…", "Point at it and press OK. Sofa remembers it for this site",
                          self.start_zap))
            n = len(ab.zaps_for(site))
            if n:
                items.append((f"Bring back {n} removed element{'s' if n != 1 else ''} on {site}", "",
                              lambda: (ab.clear_zaps(site), b.view.reload())))
        items.append(("Turn ad blocking off everywhere" if p.adblock else "Turn ad blocking on", "",
                      lambda: (setattr(p, "adblock", not p.adblock), self.adblock_changed())))
        items.append(("Filter lists…", f"{len(ab.lists())} in use, {ab.rule_count:,} rules", self.show_settings))
        items.append(("Update filter lists now", "Downloads the lists that come from the web",
                      lambda: (ab.load(refresh=True), self.toast("Updating filter lists…"))))
        self.pick(f"Ad blocking: {state}", "", items)

    def set_autoclean(self, site, on):
        self.prefs.set_in("autoclean_sites", site, on)
        self.toast(f"{site} is cleaned up by itself" if on else f"{site} is no longer cleaned up by itself")
        if on:
            self.cleanup(quiet=True)

    def cleanup(self, tab=None, quiet=False):
        """Removes full-screen overlays, cookie walls and sticky ad bars, and lets the page scroll again."""
        tab = tab or self.browser.tab
        if tab is None or not tab.uri:
            return

        def done(n):
            if not quiet:
                self.toast("Cleaned up the page" if n else "Nothing to clean up here")

        self.browser.call("cleanup", cb=done, tab=tab)

    def start_zap(self):
        if self.page != "web":
            return
        self.zap_next = True
        self.pointer.poke()
        self.toast("Remove element: point at it and press OK. Back to cancel", timeout=4)

    def take_zap(self, x, y):
        """The pointer's click, while Remove element waits for one."""
        if not self.zap_next:
            return False
        self.zap_next = False
        self.zap_at(x, y)
        return True

    def zap_at(self, x, y):
        tab = self.browser.tab
        site = site_of(host_of(tab.uri))

        def done(sel):
            if not sel:
                self.toast("Nothing to remove there")
            elif sel != "-" and self.adblock.add_zap(site, sel):
                self.toast("Removed. Ad block in the menu brings it back")
            else:
                self.toast("Removed (this page only)")

        self.browser.call("zapAt", x, y, cb=done, tab=tab)

    # Holding OK on a link, image or video
    def on_hold_menu(self, info):
        link, image, video = info.get("link"), info.get("image"), info.get("video")
        target = link or video or image
        options = []
        if link:
            options.append(("open", "Open link"))
            if self.browser.can_open_tab():
                options.append(("new_tab", "Open in new tab"))
        if video or link:
            options.append(("play", "Play in mpv"))
        if image and not link:
            options.append(("image", "Open image"))
        options.append(("download", "Download"))
        options.append(("copy", "Copy address"))
        if link and image:
            options.append(("copy_image", "Copy image address"))

        def done(r):
            v = self.browser.view
            if r == "open":
                self.browser.user_input()
                v.load_uri(link)
            elif r == "new_tab":
                self.new_tab(link)
            elif r == "image":
                v.load_uri(image)
            elif r == "play":
                src = video if video and not video.startswith("blob:") else None
                self.play({"src": src or "", "mse": not src, "frameUrl": link or info.get("frameUrl") or v.get_uri(),
                           "top": not src and bool(link), "title": info.get("text") or self.page_title, "duration": 0})
            elif r == "download":
                self.browser.download(target)
            elif r == "copy":
                self.get_clipboard().set(target)
                self.toast("Address copied")
            elif r == "copy_image":
                self.get_clipboard().set(image)
                self.toast("Image address copied")

        self.choose(info.get("text") or "", target, [("cancel", "Cancel")] + options, done)

    def on_scroll_mode(self, on):
        if on:
            self.toast("Scroll mode: arrows scroll, OK goes back to the pointer", timeout=2)

    # Watch
    def watch(self, auto=False):
        if self.page != "web" or self.watching or self.player.running:
            return
        if not auto:
            self.stop_auto_watch()
        self.watching = True
        self.auto_watching = auto
        self.candidates = []
        self.browser.call("locate")
        GLib.timeout_add(450, self._watch_chosen)

    def on_video_candidate(self, tab, c):
        if self.watching and self._is_current(tab):
            self.candidates.append(c)

    def _watch_chosen(self):
        self.watching = False
        auto = self.auto_watching
        cands = [c for c in self.candidates if c.get("width", 0) * c.get("height", 0) > 0]
        best = max(cands, key=lambda c: c["width"] * c["height"], default=None)
        if best and auto and self.prefs.auto_watch == "always":
            dur = best.get("duration", 0)
            if (best.get("muted") and best.get("playing")) or best.get("loop") or 0 < dur < 60:
                best = None
        if best is None:
            if auto:
                self._auto_watch_again()
            else:
                self.toast("No video on this page")
            return False
        self.auto_watching = False
        self.play(best)
        return False

    def play(self, c):
        """Plays a video the page has (a Watch candidate or a video the user started) in mpv, or fills the window."""
        if not self.prefs.use_player or not player_mod.available(self.prefs.player_command):
            if self.prefs.use_player:
                self.toast("Install mpv to play videos in it: sudo apt install mpv", timeout=4)
            self._fill()
            return
        tab = self.browser.tab
        page_url = tab.view.get_uri() or ""
        src = c.get("src") or ""
        direct = src.startswith(("http://", "https://")) and not c.get("mse")
        frame = c.get("frameUrl") or page_url
        if direct:
            url, referrer = src, frame
        else:
            url = frame if frame.startswith(("http://", "https://")) else page_url
            referrer = None
        self.last_own_video = time.monotonic()
        self.toast("Opening in mpv…", timeout=2)
        screen_h = self._screen_height()
        max_h = self.prefs.player_max_height or screen_h

        def go(cookies):
            self.player.play(
                url=url, page_url=page_url, title=c.get("title") or self.page_title, user_agent=self.prefs.user_agent_string(),
                cookies=cookies, referrer=referrer, direct=direct, private=self.browser.private, max_height=max_h,
                start=c.get("position", 0) if c.get("playing") else 0, subtitles=c.get("tracks") or [],
                on_started=lambda: self.browser.call("pauseAll", tab=tab),
                on_ended=lambda ok, msg: self._player_ended(ok, msg, tab))

        self.browser.cookies_for(url, go)

    def _player_ended(self, ok, msg, tab=None):
        self.present()
        GLib.idle_add(lambda: self._phone_sync() and False)
        if self.page == "web" and self.browser.view:
            self.browser.view.grab_focus()
        if not ok and (tab is None or self._is_current(tab)):
            why = f": {msg}" if msg else ""
            self.toast(f"mpv couldn't play this video{why}. The site's player carries on.", timeout=5)
            self.browser.call("resume")
            self._fill()

    def _fill(self):
        self.filled = True
        self.browser.call("fill")

    def _unfill(self):
        self.filled = False
        self.browser.call("unfill")

    def _screen_height(self):
        surface = self.get_surface()
        mon = self.get_display().get_monitor_at_surface(surface) if surface else None
        if mon is None:
            return 2160
        g = mon.get_geometry()
        return int(g.height * mon.get_scale())

    def on_video_started(self, tab, c):
        """"Open videos in mpv": the user started a video on the page with sound, a minute or longer."""
        if not self._is_current(tab):
            return
        if not (self.prefs.open_in_player and self.prefs.use_player) or self.player.running or self.watching:
            return
        if not player_mod.available(self.prefs.player_command):
            return
        if time.monotonic() - self.last_own_video < OWN_VIDEO_DEBOUNCE_S or self.page != "web":
            return
        self.play(dict(c, playing=True))

    # Watch by itself
    def maybe_auto_watch(self):
        v = self.browser.view
        uri = v.get_uri() if v else None
        host = host_of(uri)
        mode = self.prefs.auto_watch
        runs = mode == "always" and host or (mode == "sites" and self.prefs.in_set("auto_watch_sites", host))
        if not runs or uri == self.auto_watch_url:
            return
        self.auto_watch_url = uri
        self.auto_watch_tries = 0
        self._schedule_auto_watch(AUTO_WATCH_SETTLE_S)

    def _schedule_auto_watch(self, delay):
        if self.auto_watch_timer:
            GLib.source_remove(self.auto_watch_timer)
        self.auto_watch_timer = GLib.timeout_add(int(delay * 1000), self._auto_watch_try)

    def _auto_watch_try(self):
        self.auto_watch_timer = 0
        v = self.browser.view
        if self.page != "web" or self.menu_open or self.player.running or not v or v.get_uri() != self.auto_watch_url:
            return False
        self.watch(auto=True)
        return False

    def _auto_watch_again(self):
        self.auto_watching = False
        self.auto_watch_tries += 1
        if self.auto_watch_tries < AUTO_WATCH_TRIES:
            self._schedule_auto_watch(1.5)

    def stop_auto_watch(self):
        if self.auto_watch_timer:
            GLib.source_remove(self.auto_watch_timer)
            self.auto_watch_timer = 0
        if self.auto_watching:
            self.auto_watching = False
            self.watching = False

    # Keys
    def _key_pressed(self, _ctl, keyval, _code, state):
        name = Gdk.keyval_name(keyval) or ""
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        alt = bool(state & Gdk.ModifierType.ALT_MASK)
        if name == "F11":
            self.unfullscreen() if self.is_fullscreen() else self.fullscreen()
            return True
        if ctrl and name in ("q", "Q"):
            self.quit()
            return True
        if self.dialog is not None:
            return False
        if self.page == "setup":
            pass
        elif ctrl and name in ("t", "T"):
            self.close_menu()
            self.hide_search()
            if self.new_tab():
                # Ready to type an address, as in other browsers; after Home has taken the focus.
                GLib.idle_add(lambda: self.show_search("") and False)
            return True
        elif ctrl and name in ("w", "W"):
            self.close_tab(self.browser.current)
            return True
        elif ctrl and name in ("Tab", "ISO_Left_Tab", "Page_Down", "Page_Up"):
            back = name in ("ISO_Left_Tab", "Page_Up") or bool(state & Gdk.ModifierType.SHIFT_MASK)
            self.next_tab(-1 if back else 1)
            return True
        self.stop_auto_watch()
        if self.search.get_visible():
            if name in ("Escape", "XF86Back"):
                self.hide_search()
                return True
            return False
        if self.find.get_visible() and self.find.entry.has_focus():
            if name in ("Escape", "XF86Back"):
                self.hide_find()
                return True
            return False
        if self.menu_open:
            if name in BACK_KEYS or name in MENU_KEYS:
                self.close_menu()
                return True
            return False
        if name in SEARCH_KEYS or (ctrl and name in ("l", "L")):
            self.show_search(self.browser.view.get_uri() if self.page == "web" and self.browser.view else "")
            return True
        if name in HOME_KEYS or (alt and name == "Home"):
            self.show_home()
            return True
        if self.page != "web":
            focus = self.get_focus()
            typing = isinstance(focus, Gtk.Text) or isinstance(focus, Gtk.Editable)
            if name in ("Escape", "XF86Back") or (name == "BackSpace" and not typing):
                if self.page == "setup":
                    return True
                self.go_back_from_page()
                return True
            return False
        return self._web_key(name, ctrl, alt)

    def _web_key(self, name, ctrl, alt):
        v = self.browser.view
        self.browser.user_input()
        if self.find.get_visible() and name == "Escape":
            self.hide_find()
            return True
        if ctrl:
            if name in ("f", "F"):
                self.show_find()
                return True
            if name in ("d", "D"):
                self.on_menu_action("bookmark")
                return True
            if name in ("plus", "equal", "KP_Add"):
                self.on_menu_action("zoom_in")
                return True
            if name in ("minus", "KP_Subtract"):
                self.on_menu_action("zoom_out")
                return True
            if name in ("0", "KP_0"):
                v.set_zoom_level(self.prefs.zoom / 100)
                return True
            if name in ("r", "R"):
                v.reload()
                return True
            return False
        if alt and name == "Left":
            v.can_go_back() and v.go_back()
            return True
        if alt and name == "Right":
            v.can_go_forward() and v.go_forward()
            return True
        if name == "F5":
            v.reload()
            return True
        if name == "XF86Forward":
            v.can_go_forward() and v.go_forward()
            return True
        if self.editing:
            if name in ("Escape", "XF86Back"):
                self.browser.call("blur")
                self.editing = False
                return True
            return False
        if self.web_fullscreen and name == "Escape":
            return False  # WebKit leaves fullscreen
        if self.zap_next and name in BACK_KEYS:
            self.zap_next = False
            self.toast("Remove element cancelled", timeout=2)
            return True
        if name in BACK_KEYS or name in MENU_KEYS:
            if self.pointer.scroll_mode:
                self.pointer.set_scroll_mode(False)
                return True
            if self.filled and name in BACK_KEYS:
                self._unfill()
                return True
            if name in BACK_KEYS and self.prefs.back_goes_back and v.can_go_back():
                v.go_back()
                return True
            self.open_menu()
            return True
        if name in PLAY_KEYS:
            self.watch()
            return True
        if name in ("Page_Up", "Page_Down", "XF86ChannelUp", "XF86ChannelDown"):
            self.browser.call("pageScroll", -1 if name in ("Page_Up", "XF86ChannelUp") else 1)
            return True
        if self.prefs.in_set("pointer_off_sites", self.browser.host):
            return False
        if name in DIRECTION_KEYS or name in OK_KEYS:
            return self.pointer.key_pressed(name)
        return False

    def _key_released(self, _ctl, keyval, _code, _state):
        name = Gdk.keyval_name(keyval) or ""
        if self.page == "web" and (name in DIRECTION_KEYS or name in OK_KEYS):
            self.pointer.key_released(name)

    def _real_mouse_moved(self, _ctl, x, y):
        last = self.real_mouse
        self.real_mouse = (x, y)
        if last and (abs(last[0] - x) > 3 or abs(last[1] - y) > 3) and self.pointer.visible:
            self.pointer.x, self.pointer.y = x, y
            self.pointer.hide_now()

    # Game pads
    def _pad_action(self, action, pressed):
        if self.player.running or not self.is_active():
            return
        self._remote(action, pressed)

    def _remote(self, action, pressed=True):
        """A game pad's or the phone's button: the pointer on a page, focus moves on Sofa's own screens."""
        directions = {"up": "Up", "down": "Down", "left": "Left", "right": "Right"}
        in_page = self.page == "web" and not self.menu_open and self.dialog is None and not self.search.get_visible()
        if in_page and not self.editing and not self.prefs.in_set("pointer_off_sites", self.browser.host):
            if action in directions:
                (self.pointer.key_pressed if pressed else self.pointer.key_released)(directions[action])
                return
            if action == "select":
                (self.pointer.key_pressed if pressed else self.pointer.key_released)("Return")
                return
        if not pressed:
            return
        if action in directions:
            d = {"up": Gtk.DirectionType.UP, "down": Gtk.DirectionType.DOWN, "left": Gtk.DirectionType.LEFT,
                 "right": Gtk.DirectionType.RIGHT}[action]
            if not self.child_focus(d):
                self.child_focus(Gtk.DirectionType.TAB_FORWARD if d in (Gtk.DirectionType.DOWN, Gtk.DirectionType.RIGHT)
                                 else Gtk.DirectionType.TAB_BACKWARD)
            return
        if action == "select":
            f = self.get_focus()
            if f is not None:
                f.activate()
            return
        if action == "back":
            if self.dialog is not None:
                self.dialog.force_close()
                return
            self._key_pressed(None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0))
            return
        if self.dialog is not None:
            return
        if action == "menu":
            self.close_menu() if self.menu_open else self.open_menu()
        elif action == "watch":
            self.watch()
        elif action == "search":
            self.show_search_from_menu()
        elif action == "home":
            self.show_home()
        elif action == "history_back" and self.page == "web" and self.browser.view.can_go_back():
            self.browser.view.go_back()
        elif action == "history_forward" and self.page == "web" and self.browser.view.can_go_forward():
            self.browser.view.go_forward()
        elif action in ("page_up", "page_down") and self.page == "web":
            self.browser.call("pageScroll", -1 if action == "page_up" else 1)

    def _pad_sticks(self, aim, wheel):
        if self.page == "web" and not self.menu_open and self.dialog is None and not self.player.running:
            self.pointer.set_aim(*aim)
            self.pointer.set_wheel(*wheel)
        else:
            self.pointer.set_aim(0, 0)
            self.pointer.set_wheel(0, 0)

    # The phone remote
    def _phone_sync(self):
        """The server runs while it is turned on and Sofa is on screen."""
        suspended = self.is_suspended() if hasattr(self, "is_suspended") else False
        # mpv covering the window can make it count as suspended: the phone still controls mpv then.
        want = self.prefs.phone_remote and self.get_mapped() and (not suspended or self.player.running)
        if want and not self.phone.running:
            try:
                if not self.phone.start():
                    self.toast("Phone remote: ports 8765 to 8768 are all in use")
            except phone.CertUnavailable as e:
                # Never quietly falls back to plain HTTP: that is the user's choice, in Settings.
                self.toast(f"Phone remote is off: {e}", timeout=6)
        elif not want and self.phone.running:
            self.phone.stop()
        self._update_menu()

    def phone_changed(self):
        self._phone_sync()

    def phone_security_changed(self):
        """Plain HTTP turned on or off: the server starts again with the other kind of address."""
        if not self.prefs.phone_plain_http:
            self.phone_tokens.rotate()  # the token may have crossed the network in the clear
        if self.phone.running:
            self.phone.stop(wait=True)
        self._phone_sync()
        if self.phone.running:
            self.toast("Phone remote: scan the new QR code")

    def show_pairing(self):
        if not self.prefs.phone_remote:
            self.prefs.phone_remote = True
        self._phone_sync()
        url = self.phone.url()
        if not url:
            self.toast("Not connected to a network" if self.phone.running else "The phone remote could not start")
            return
        box = Gtk.Box(spacing=32)
        code = QrCode(int(300 * float(self.prefs.ui_scale)))
        box.append(code)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER, hexpand=True)
        text.append(label("Scan this with your phone's camera. The page that opens is a trackpad, a keyboard and a "
                          "remote for Sofa: open websites, type into the TV, and press OK, Back or Watch.",
                          wrap=True))
        text.append(label("The phone must be on the same network. It works while Sofa is open.", "sofa-hint",
                          wrap=True))
        if self.phone.scheme == "https":
            text.append(label("The first time, the phone warns that the connection isn't private: Sofa makes its "
                              "own certificate, and no authority vouches for it. That's expected; continue to the "
                              "page (Advanced, then Proceed; or Show Details, then visit this website).",
                              "sofa-hint", wrap=True))
        addr = label(f"Or open: {url}", "sofa-hint", wrap=True)
        addr.set_selectable(True)
        text.append(addr)
        shortcut = url.replace("/?t=", "/open?t=")
        text.append(label(f"To send links from an iPhone's share sheet, a Shortcut can post the link to: {shortcut}",
                          "sofa-hint", wrap=True))
        box.append(text)
        box.set_size_request(int(820 * float(self.prefs.ui_scale)), -1)
        d = self.choose("Pair phone", "", [("done", "Done"), ("forget", "Forget paired phones")],
                        lambda r: r == "forget" and self.forget_phones(), destructive="forget", extra=box)
        d.set_content_width(int(1000 * float(self.prefs.ui_scale)))
        qr.matrix(url, code.set_rows)

    def forget_phones(self):
        self.phone_tokens.rotate()
        self.toast("Paired phones forgotten: scan the new QR code")

    def _phone_in_page(self):
        return (self.page == "web" and not self.menu_open and self.dialog is None and not self.search.get_visible()
                and not (self.find.get_visible() and self.find.entry.has_focus()))

    def _gtk_text(self):
        f = self.get_focus()
        return f if isinstance(f, Gtk.Editable) else None

    def phone_open(self, text):
        if self.player.running:
            self.player.stop()
        self.close_dialog()
        self.close_menu()
        if self.search.get_visible():
            self.hide_search()
        self.open_url(text)
        self.present()

    def phone_type(self, text):
        if not text:
            return
        ed = self._gtk_text()
        if ed is not None:
            bounds = ed.get_selection_bounds()
            if bounds:
                ed.delete_text(*bounds)
            pos = ed.insert_text(text.replace("\n", " "), ed.get_position())
            ed.set_position(pos if isinstance(pos, int) else -1)
            return
        if not self._phone_in_page() or self.player.running:
            self.toast("Open a text box first")
            return
        b = self.browser
        x, y = self.pointer.css_pos() if self.pointer.visible else (None, None)

        def ready(r):
            if r in ("ok", "iframe"):
                b.view.execute_editing_command_with_argument("InsertText", text)
            else:
                self.toast("Select a text box on the TV first")

        b.call("prepareType", x, y, cb=ready)

    def phone_key(self, name):
        self.browser.user_input()
        if self.player.running:
            self.player.send_key(name)
            return
        ed = self._gtk_text()
        if name in ("enter", "backspace"):
            if ed is not None:
                if name == "enter":
                    ed.activate()
                else:
                    pos = ed.get_position()
                    start, end = ed.get_selection_bounds() or (pos, pos)
                    if start != end or start > 0:
                        ed.delete_text(start - 1 if start == end else start, end)
            elif self._phone_in_page():
                if name == "enter":
                    self.browser.call("enter")
                else:
                    self.browser.view.execute_editing_command("DeleteBackward")
            return
        if name == "watch":
            if self.page == "web":
                self.close_menu()
                self.watch()
            return
        if name == "playpause":
            if self.page == "web":
                self.browser.call("media", "playpause")
            return
        if name == "home":
            self.close_dialog()
            self.show_home()
            return
        in_page = self._phone_in_page()
        if in_page and self.editing and name in EDIT_MOVES:
            self.browser.view.execute_editing_command(EDIT_MOVES[name])
            return
        if in_page and not self.editing and not self.prefs.in_set("pointer_off_sites", self.browser.host):
            if name in PHONE_DIRS:
                self.pointer.step(PHONE_DIRS[name], PHONE_STEP)
                return
            if name == "ok":
                self.pointer.tap()
                return
        if name == "menu" and self.dialog is None:
            self.close_menu() if self.menu_open else self.open_menu()
            return
        self._remote({"ok": "select"}.get(name, name), True)

    def phone_pointer(self, op, a, b):
        self.browser.user_input()
        if self.player.running:
            if op == "c":
                self.player.send_key("ok")
            return
        unit = max(self.get_width(), 1) / 1920
        p = self.pointer
        if self._phone_in_page():
            if op == "m":
                p.move_by(a * unit, b * unit)
            elif op == "s":
                p._scroll(a * PHONE_SCROLL_GAIN * unit, b * PHONE_SCROLL_GAIN * unit)
                p.poke()
            elif op == "c":
                p.tap()
            elif op == "2":
                p.double_tap()
            elif op == "d":
                p.press()
            elif op == "u":
                p.release()
            elif op == "l":
                def answered(info):
                    if info and (info.get("link") or info.get("image") or info.get("video")):
                        self.on_hold_menu(info)
                p.poke()
                self.browser.hit(*p.css_pos(), answered)
            elif op == "z":
                self.on_menu_action("zoom_in" if a > 0 else "zoom_out")
            elif op == "x":
                self.zap_next = False
                self.zap_at(*p.css_pos())
            elif op == "k":
                self.cleanup()
            return
        # Sofa's own screens: the trackpad moves the focus, a tap presses what has it.
        if op in "ms":
            acc = self._pad_acc
            acc[0] += a
            acc[1] += b
            step = PHONE_FOCUS_STEP * (0.6 if op == "s" else 1)
            while abs(acc[0]) >= step or abs(acc[1]) >= step:
                if abs(acc[1]) >= abs(acc[0]):
                    d = 1 if acc[1] > 0 else -1
                    acc[1] -= d * step
                    self._remote("down" if d > 0 else "up")
                else:
                    d = 1 if acc[0] > 0 else -1
                    acc[0] -= d * step
                    self._remote("right" if d > 0 else "left")
        elif op == "c":
            self._pad_acc = [0.0, 0.0]
            self._remote("select")

    # Dialogs and notices
    def choose(self, heading, body, responses, on_done, destructive=None, extra=None, default=None):
        def closed():
            if self.dialog is d:
                self.dialog = None
            if self.page == "web" and self.browser.view and not self.search.get_visible():
                self.browser.view.grab_focus()

        d = alert(self, heading, body, responses, on_done, destructive=destructive, extra=extra, on_closed=closed,
                  default=default)
        self.dialog = d
        return d

    def pick(self, heading, body, items):
        """A list of choices for the remote (see ui.choose_list)."""
        def closed():
            if self.dialog is d:
                self.dialog = None
            if self.page == "web" and self.browser.view and not self.search.get_visible():
                self.browser.view.grab_focus()

        d = choose_list(self, heading, body, items, on_closed=closed, width=int(560 * float(self.prefs.ui_scale)))
        self.dialog = d
        return d

    def close_dialog(self):
        if self.dialog is not None:
            self.dialog.force_close()

    def toast(self, text, timeout=3):
        t = Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout)
        self.toasts.add_toast(t)

    # Settings hooks
    def adblock_changed(self):
        self.browser.refresh_content()
        if self.browser.view and self.browser.view.get_uri():
            self.browser.view.reload()

    def clear_browsing_data(self):
        def go(r):
            if r == "clear":
                self.db.clear_history()
                self.browser.clear_data(lambda: self.toast("Browsing data cleared"))

        self.choose("Clear browsing data?", "Cookies, site data, cache and history. Bookmarks and Home stay.",
                    [("cancel", "Cancel"), ("clear", "Clear")], go, destructive="clear")

    # Ending
    def quit(self):
        self.close()

    def _close_request(self, _w):
        self.player.stop()
        self.phone.stop()
        if self.prefs.clear_on_exit:
            self.prefs.tabs = []
        else:
            self._save_tabs()
        if self.prefs.clear_on_exit and not getattr(self, "_cleared", False):
            self.db.clear_history()

            def done():
                self._cleared = True
                self.close()

            self.browser.clear_data(done)
            return True
        return False


class SofaApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.win = None

    def do_activate(self):
        if self.win is None:
            self.win = Window(self)
        self.win.present()

    def do_open(self, files, _n, _hint):
        self.do_activate()
        if files:
            self.win.open_url(files[0].get_uri())


def main(argv=None):
    GLib.set_prgname("sofa-tv-browser")
    GLib.set_application_name("Sofa")
    return SofaApp().run(argv or sys.argv)
