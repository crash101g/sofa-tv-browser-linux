# SPDX-License-Identifier: GPL-3.0-only
"""The tabs' web views: WebKitGTK set up for privacy, the page script, ad blocking per tab, pop-ups, and downloads."""

import json
import os
import re
import secrets
import time
from urllib.parse import quote, urlsplit

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, WebKit  # noqa: E402

from . import paths  # noqa: E402
from .adblock import youtube_script, zap_sheets  # noqa: E402
from .db import host_of, site_of  # noqa: E402

WORLD = "sofa"
GESTURE_S = 1.5         # a window or navigation this soon after a press counts as the user's
ALLOW_ONCE_S = 60.0
MAX_TABS = 6
MAX_DIALOGS = 2         # alert, confirm and prompt boxes a page may show before the rest are dismissed
BLOCKED_PAGE = """<!doctype html><meta charset="utf-8"><title>Blocked</title>
<body style="font:24px system-ui,sans-serif;background:#1e1e1e;color:#eee;display:grid;place-items:center;height:90vh">
<div style="max-width:900px"><h1 style="font-size:40px">Sofa blocked this page</h1>
<p>{host} is on one of Sofa's ad and tracker lists.</p>
<p style="opacity:.7">To open it anyway, press Back, choose "Ad block" in the menu and turn it off for this site.</p>
</div>"""


def looks_like_url(text: str) -> bool:
    t = text.strip()
    if not t or " " in t:
        return False
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", t) or t.startswith(("about:", "file:", "data:")):
        return True
    host = t.split("/", 1)[0]
    if host.startswith("localhost") or re.match(r"^\d{1,3}(\.\d{1,3}){3}(:\d+)?$", host):
        return True
    return bool(re.match(r"^[^\s.]+(\.[^\s.]+)+(:\d+)?$", host)) and not host.endswith(".")


def to_url(text: str, engine) -> str:
    t = text.strip()
    if looks_like_url(t):
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", t) and not re.match(r"^[^:/]+:\d+", t):
            return t
        return ("http://" if t.startswith(("localhost", "127.", "192.168.", "10.")) else "https://") + t
    return engine.search_url(t)


class Tab:
    """One tab: its web view and what Sofa knows about the page in it."""

    def __init__(self, view, private):
        self.view = view
        self.ucm = view.get_user_content_manager()
        self.private = private
        self.editing = False
        self.filled = False
        self.pending_url = None     # the last pop-up blocked, for "Allow once"
        self.pending_at = 0.0
        self.hit_cb = None
        self.restore_url = None     # a tab reopened at start loads when it is first shown
        self.restore_title = ""
        self.dialogs = 0
        self.keys = {}              # what was last put in its user content manager
        self.opened_by = None       # the tab a right-click opened it from, in the background

    @property
    def uri(self):
        return self.view.get_uri() or self.restore_url or ""

    @property
    def title(self):
        return self.view.get_title() or self.restore_title or ""

    def has_pending(self):
        return self.pending_url is not None and time.monotonic() - self.pending_at < ALLOW_ONCE_S

    def take_pending(self):
        url = self.pending_url if self.has_pending() else None
        self.pending_url = None
        return url


class Browser:
    """
    The tabs, normal or private (private browsing has a tab set of its own), and what they share: the network
    sessions, the page script and ad blocking. [ui] gets the tabs' events, with the tab first.
    """

    def __init__(self, prefs, adblocker, db, ui):
        self.prefs = prefs
        self.adblock = adblocker
        self.db = db
        self.ui = ui
        self.token = secrets.token_hex(16)
        with open(paths.resource("page.js"), encoding="utf-8") as f:
            self.page_script = f.read().replace("__TOKEN__", self.token)
        data = paths.data_dir()
        cache = paths.cache_dir()
        self.session = WebKit.NetworkSession.new(paths.sub(data, "web"), paths.sub(cache, "web"))
        self._setup_session(self.session, persistent=True)
        self.private_session = None
        self.tabs: list[Tab] = []
        self.current = 0
        self.private = False
        self.saved = None           # the normal tabs while private browsing is on: (tabs, current)
        self.last_input = 0.0
        self.typed_navigation = False
        self.adblock.connect(self._filters_changed)
        prefs.connect(self._pref_changed)

    # Sessions
    def _setup_session(self, session, persistent):
        cm = session.get_cookie_manager()
        if persistent:
            cm.set_persistent_storage(os.path.join(paths.sub(paths.data_dir(), "web"), "cookies.sqlite"),
                                      WebKit.CookiePersistentStorage.SQLITE)
        self._apply_cookie_policy(session)
        session.set_itp_enabled(True)
        dm = session.get_website_data_manager()
        try:
            dm.set_favicons_enabled(True)
        except (AttributeError, TypeError):
            pass
        session.connect("download-started", self._on_download)

    def _apply_cookie_policy(self, session):
        cm = session.get_cookie_manager()
        if not self.prefs.cookies:
            cm.set_accept_policy(WebKit.CookieAcceptPolicy.NEVER)
        elif self.prefs.block_3p_cookies:
            cm.set_accept_policy(WebKit.CookieAcceptPolicy.NO_THIRD_PARTY)
        else:
            cm.set_accept_policy(WebKit.CookieAcceptPolicy.ALWAYS)

    def session_for(self, tab):
        return self.private_session if tab and tab.private and self.private_session else self.session

    # Tabs
    @property
    def tab(self) -> Tab | None:
        return self.tabs[self.current] if 0 <= self.current < len(self.tabs) else None

    @property
    def view(self) -> WebKit.WebView | None:
        return self.tab.view if self.tab else None

    def all_tabs(self):
        return self.tabs + (self.saved[0] if self.saved else [])

    def tab_of(self, view=None, ucm=None) -> Tab | None:
        return next((t for t in self.all_tabs() if t.view is view or (ucm is not None and t.ucm is ucm)), None)

    def make_tab(self, private: bool, related: Tab | None = None) -> Tab:
        """A new tab, not yet in the tab list. A pop-up shares its opener's web process ([related])."""
        if private and self.private_session is None:
            self.private_session = WebKit.NetworkSession.new_ephemeral()
            self._setup_session(self.private_session, persistent=False)
        ucm = WebKit.UserContentManager()
        ucm.register_script_message_handler("sofa", WORLD)
        ucm.connect("script-message-received::sofa", self._on_message)
        if related is not None:
            view = WebKit.WebView(related_view=related.view, user_content_manager=ucm)
        else:
            view = WebKit.WebView(network_session=self.private_session if private else self.session,
                                  user_content_manager=ucm)
        tab = Tab(view, private)
        cursors = getattr(self.ui, "cursors", None)
        if cursors is not None:
            cursors.track(view)  # the page's own cursors (a hand over links, a text bar), big
        self._configure(view)
        self._set_content(tab, None)
        view.connect("decide-policy", self._on_decide_policy)
        view.connect("create", self._on_create)
        view.connect("load-changed", self._on_load_changed)
        view.connect("notify::title", lambda v, _p: self._tell("on_title", v, v.get_title()))
        view.connect("notify::uri", lambda v, _p: self._tell("on_uri", v, v.get_uri()))
        view.connect("notify::estimated-load-progress",
                     lambda v, _p: self._tell("on_progress", v, v.get_estimated_load_progress()))
        view.connect("notify::favicon", self._on_favicon)
        view.connect("permission-request", self._on_permission)
        view.connect("load-failed-with-tls-errors", self._on_tls)
        view.connect("load-failed", self._on_load_failed)
        view.connect("script-dialog", self._on_script_dialog)
        view.connect("enter-fullscreen", lambda v: self._tell("on_fullscreen", v, True) or False)
        view.connect("leave-fullscreen", lambda v: self._tell("on_fullscreen", v, False) or False)
        view.connect("web-process-terminated", lambda v, reason: self._tell("on_crash", v, reason))
        view.connect("close", lambda v: self._tell("on_close_request", v))
        view.connect("context-menu", self._on_context_menu)
        click = Gtk.GestureClick(button=0)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", lambda *a: self.user_input())
        view.add_controller(click)
        view.set_hexpand(True)
        view.set_vexpand(True)
        return tab

    def _tell(self, name, view, *args):
        tab = self.tab_of(view)
        if tab is not None:
            getattr(self.ui, name)(tab, *args)

    def _configure(self, view):
        s = view.get_settings()
        s.set_enable_javascript(self.prefs.javascript)
        # Windows are opened by Sofa's own rules (see _on_create), so WebKit must ask for every one.
        s.set_javascript_can_open_windows_automatically(True)
        s.set_media_playback_requires_user_gesture(not self.prefs.autoplay)
        s.set_enable_fullscreen(True)
        s.set_enable_developer_extras(True)
        s.set_enable_smooth_scrolling(True)
        s.set_enable_back_forward_navigation_gestures(False)
        s.set_enable_encrypted_media(True)
        s.set_enable_media_stream(False)
        s.set_enable_webrtc(True)
        s.set_enable_webaudio(True)
        s.set_enable_webgl(True)
        s.set_enable_mediasource(True)
        s.set_enable_dns_prefetching(False)
        s.set_user_agent(self.prefs.user_agent_string())
        view.set_zoom_level(self.prefs.zoom / 100)

    def reconfigure(self):
        for t in self.all_tabs():
            self._configure(t.view)
        for sess in (self.session, self.private_session):
            if sess:
                self._apply_cookie_policy(sess)

    # Ad blocking, removed elements and the page scripts, per tab
    def adblock_on_for(self, url) -> bool:
        return self.prefs.adblock and not self.prefs.in_set("adblock_off_sites", host_of(url))

    def adblock_on_here(self):
        return self.adblock_on_for(self.tab.uri) if self.tab else self.prefs.adblock

    def _set_content(self, tab, url):
        on = self.adblock_on_for(url) if url else self.prefs.adblock
        key = (on, id(self.adblock.filters))
        if tab.keys.get("filters") != key:
            tab.keys["filters"] = key
            self.adblock.apply(tab.ucm, on)
        site = site_of(host_of(url))
        zaps = tuple(self.adblock.zaps_for(site)) if on and site else ()
        if tab.keys.get("sheets", ()) != (site, zaps) and (zaps or tab.keys.get("sheets")):
            tab.keys["sheets"] = (site, zaps)
            tab.ucm.remove_all_style_sheets()
            for sheet in zap_sheets({site: list(zaps)}):
                tab.ucm.add_style_sheet(sheet)
        yt = self.prefs.adblock and self.prefs.youtube_ads and not self.prefs.in_set("adblock_off_sites", "youtube.com")
        scripts = (yt, self.prefs.send_gpc)
        if tab.keys.get("scripts") != scripts:
            tab.keys["scripts"] = scripts
            tab.ucm.remove_all_scripts()
            tab.ucm.add_script(WebKit.UserScript.new_for_world(
                self.page_script, WebKit.UserContentInjectedFrames.ALL_FRAMES, WebKit.UserScriptInjectionTime.START,
                WORLD, None, None))
            if self.prefs.send_gpc:
                tab.ucm.add_script(WebKit.UserScript.new(
                    "try{Object.defineProperty(Navigator.prototype,'globalPrivacyControl',{get:()=>true,configurable:true})}catch(e){}",
                    WebKit.UserContentInjectedFrames.ALL_FRAMES, WebKit.UserScriptInjectionTime.START, None, None))
            if yt:
                tab.ucm.add_script(youtube_script())

    def refresh_content(self, tab=None, url=None):
        """After a change to ad blocking for [tab]'s site (or to the lists): applies to its next load."""
        for t in [tab] if tab else self.all_tabs():
            self._set_content(t, url if url is not None else t.view.get_uri())

    def _filters_changed(self):
        self.refresh_content()

    def _pref_changed(self, key):
        if key in ("zaps", "youtube_ads", "adblock", "adblock_off_sites", "send_gpc"):
            self.refresh_content()

    @property
    def host(self):
        return host_of(self.tab.uri) if self.tab else None

    @property
    def zoom(self):
        return self.view.get_zoom_level() if self.view else 1.0

    # Loading
    def load(self, text: str, tab=None):
        tab = tab or self.tab
        self.typed_navigation = True
        self.user_input()
        tab.restore_url = None
        tab.view.load_uri(to_url(text, self.prefs.engine))

    def ensure_loaded(self, tab):
        """A tab reopened at start loads its page the first time it is shown."""
        if tab.restore_url and not tab.view.get_uri():
            url, tab.restore_url = tab.restore_url, None
            tab.view.load_uri(url)

    def user_input(self):
        self.last_input = time.monotonic()

    def gesture(self, action=None):
        if action is not None and action.is_user_gesture():
            return True
        return time.monotonic() - self.last_input < GESTURE_S

    # Script calls
    def js(self, code: str, cb=None, tab=None):
        tab = tab or self.tab
        if not tab:
            if cb:
                cb(None)
            return

        def done(view, res):
            value = None
            try:
                r = view.evaluate_javascript_finish(res)
                if r is not None and not r.is_undefined() and not r.is_null():
                    value = json.loads(r.to_json(0) or "null")
            except (GLib.Error, ValueError):
                pass
            if cb:
                cb(value)

        tab.view.evaluate_javascript(code, -1, WORLD, None, None, done)

    def call(self, fn: str, *args, cb=None, tab=None):
        self.js(f"window.__sofa && window.__sofa.{fn}({','.join(json.dumps(a) for a in args)})", cb, tab)

    # The pointer's page interface
    def hover(self, x, y, buttons=0):
        self.call("hover", x, y, buttons)

    def click(self, x, y):
        self.user_input()
        if self.ui.take_zap(x, y):
            return
        self.call("click", x, y)

    def dbl(self, x, y):
        self.user_input()
        self.call("dbl", x, y)

    def down(self, x, y):
        self.user_input()
        self.call("down", x, y)

    def up(self, x, y):
        self.user_input()
        self.call("up", x, y)

    def scroll(self, x, y, dx, dy, done=None):
        self.call("scroll", x, y, dx, dy, cb=lambda _v: done and done())

    def hit(self, x, y, cb):
        tab = self.tab
        if not tab:
            cb(None)
            return
        tab.hit_cb = cb
        self.call("hit", x, y)

        def timeout():
            if tab.hit_cb is cb:
                tab.hit_cb = None
                cb(None)
            return False

        GLib.timeout_add(400, timeout)

    # Messages from the page script
    def _on_message(self, ucm, value):
        tab = self.tab_of(ucm=ucm)
        if tab is None:
            return
        try:
            msg = json.loads(value.to_json(0))
        except (ValueError, GLib.Error, AttributeError):
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind == "hit":
            cb, tab.hit_cb = tab.hit_cb, None
            if cb:
                cb(msg)
        elif kind == "focus":
            tab.editing = bool(msg.get("editable"))
            self.ui.on_editing(tab, tab.editing)
        elif kind == "video":
            self.ui.on_video_candidate(tab, msg)
        elif kind == "started":
            self.ui.on_video_started(tab, msg)
        elif kind == "select":
            self.ui.on_select(tab, msg.get("options") or [], msg.get("selected", -1))

    # Pop-ups
    def _block(self, tab, url):
        tab.pending_url = url
        tab.pending_at = time.monotonic()
        self.ui.on_blocked(tab, url)

    def _on_create(self, view, action):
        """
        A page asks for a new window. With ad blocking on: windows opened without a press, and windows to listed ad
        sites, are blocked. After a press, a window to the same site opens in a new tab, and a window to another
        site asks first: the click-anywhere pop-under trick is as common as a real link.
        """
        tab = self.tab_of(view)
        if tab is None:
            return None
        uri = action.get_request().get_uri() or ""
        target = host_of(uri)
        active = self.adblock_on_for(view.get_uri())
        if active and (not self.gesture(action) or self.adblock.blocks_popup(target)):
            self._block(tab, uri)
            return None
        if len(self.tabs) >= MAX_TABS or tab not in self.tabs:
            if uri and uri != "about:blank":
                GLib.idle_add(lambda: view.load_uri(uri) or False)
            self.ui.on_tab_limit(tab)
            return None
        same = not target or site_of(target) == site_of(host_of(view.get_uri()))
        # A middle click or Ctrl+click on a link is meant: no question.
        meant = action.get_mouse_button() == 2 or bool(action.get_modifiers() & Gdk.ModifierType.CONTROL_MASK)
        new = self.make_tab(tab.private, related=tab)
        new.view.connect("ready-to-show", lambda v: self.ui.on_popup(tab, new, uri, ask=active and not same and not meant))
        return new.view

    def _on_context_menu(self, view, _menu, hit):
        """A right-click on a link opens it in a new tab in the background; elsewhere, WebKit's own menu."""
        uri = hit.get_link_uri() if hit.context_is_link() else None
        tab = self.tab_of(view)
        if uri and uri.startswith(("http://", "https://")) and tab is not None:
            self.user_input()
            self.ui.open_in_background(tab, uri)
            return True
        return False

    def _on_decide_policy(self, view, decision, kind):
        # Redirects to listed ad sites need no check here: the content filters stop document loads to them too.
        if kind == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            action = decision.get_navigation_action()
            uri = action.get_request().get_uri() or ""
            if not uri or uri.startswith(("http://", "https://", "about:", "file:", "data:", "blob:")):
                return False
            # Other schemes (mailto:, magnet:, …) go to the system, but only when the user pressed something.
            if self.gesture(action):
                Gio.AppInfo.launch_default_for_uri_async(uri, None, None, None, None)
            decision.ignore()
            return True
        if kind == WebKit.PolicyDecisionType.RESPONSE and decision.is_main_frame_main_resource():
            if not decision.is_mime_type_supported():
                decision.download()
                return True
            headers = decision.get_response().get_http_headers()
            cd = (headers.get_one("Content-Disposition") or "") if headers is not None else ""
            if cd.lower().startswith("attachment"):
                decision.download()
                return True
        return False

    def _on_load_changed(self, view, event):
        tab = self.tab_of(view)
        if tab is None:
            return
        uri = view.get_uri() or ""
        if event == WebKit.LoadEvent.STARTED:
            tab.dialogs = 0
            tab.editing = False
            tab.filled = False
            self._set_content(tab, uri)
        elif event == WebKit.LoadEvent.COMMITTED:
            self.typed_navigation = False
            self._set_content(tab, uri)  # after a redirect to another site
            if not tab.private and uri.startswith(("http://", "https://")) and self.prefs.history_days != 0:
                self.db.visit(uri, view.get_title())
        elif event == WebKit.LoadEvent.FINISHED:
            if not tab.private and view.get_title():
                self.db.set_title(uri, view.get_title())
        self.ui.on_load_changed(tab, event)

    def _on_script_dialog(self, view, dialog):
        """
        A page gets two alert, confirm or prompt boxes per page load, answered in Sofa's own dialog; more are dismissed.
        "Leave this page?" never traps Back.
        """
        tab = self.tab_of(view)
        kind = dialog.get_dialog_type()
        if kind == WebKit.ScriptDialogType.BEFORE_UNLOAD_CONFIRM:
            dialog.confirm_set_confirmed(True)
            return True
        if tab is None:
            return False
        tab.dialogs += 1
        if tab.dialogs > MAX_DIALOGS or not self.ui.on_script_dialog(tab, dialog):
            if kind == WebKit.ScriptDialogType.CONFIRM:
                dialog.confirm_set_confirmed(False)
        return True

    def _on_favicon(self, view, _p):
        tab = self.tab_of(view)
        tex = view.get_favicon()
        host = host_of(view.get_uri())
        if tex is None or not host or tab is None or tab.private:
            return
        path = os.path.join(paths.sub(paths.cache_dir(), "favicons"), quote(host, safe="") + ".png")
        try:
            tex.save_to_png(path)
            self.ui.on_favicon(tab, host)
        except GLib.Error:
            pass

    def _on_permission(self, view, request):
        # Pages get nothing extra: no camera, microphone, location, notifications or files.
        if isinstance(request, WebKit.MediaKeySystemPermissionRequest):
            request.allow()  # protected video, as far as WebKitGTK can play it
            return True
        request.deny()
        return True

    def _on_tls(self, view, uri, certificate, errors):
        """Only the page the user is going to may ask to go on despite a broken certificate."""
        tab = self.tab_of(view)
        host = urlsplit(uri).hostname

        def go_on():
            self.session_for(tab).allow_tls_certificate_for_host(certificate, host)
            view.load_uri(uri)

        if tab is not None:
            self.ui.ask_tls(tab, host, go_on)
        return True

    def _on_load_failed(self, view, event, uri, error):
        # A page on a listed ad or tracker domain: say so, rather than leave the screen blank.
        if error.domain == "WebKitPolicyError" and error.code == 104:
            host = GLib.markup_escape_text(host_of(uri) or uri)
            view.load_alternate_html(BLOCKED_PAGE.replace("{host}", host), uri, None)
            return True
        return False  # WebKit's own error page

    # Downloads
    def _on_download(self, _session, download):
        self.ui.on_download(download)

    def download(self, url):
        if self.view:
            self.view.download_uri(url)

    def cookies_for(self, url, cb):
        def done(cm, res):
            try:
                cb(cm.get_cookies_finish(res) or [])
            except GLib.Error:
                cb([])

        self.session_for(self.tab).get_cookie_manager().get_cookies(url, None, done)

    def clear_data(self, cb=None):
        def done(dm, res):
            try:
                dm.clear_finish(res)
            except GLib.Error:
                pass
            if cb:
                cb()

        self.session.get_website_data_manager().clear(WebKit.WebsiteDataTypes.ALL, 0, None, done)

    def end_private(self):
        self.private_session = None
