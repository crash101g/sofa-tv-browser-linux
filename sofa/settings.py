# SPDX-License-Identifier: GPL-3.0-only
"""Settings and the first-start setup."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from . import __version__, player  # noqa: E402
from .adblock import CATALOG, STRONGER_IDS  # noqa: E402
from .prefs import ENGINES, HISTORY_CHOICES, UA_LABELS  # noqa: E402
from .cursors import SIZES as MOUSE_SIZES  # noqa: E402
from .ui import label  # noqa: E402

SCALES = [(1.0, "Small"), (1.25, "Medium"), (1.5, "Large (TV)"), (1.75, "Larger"), (2.0, "Largest")]
THEMES = [("dark", "Dark"), ("light", "Light"), ("system", "As the system")]
SPEEDS = [(1, "Slow"), (2, "Normal"), (3, "Fast")]
ZOOMS = [(z, f"{z}%") for z in (67, 75, 90, 100, 110, 125, 150, 175, 200)]
QUALITIES = [(0, "The screen's"), (2160, "4K"), (1440, "1440p"), (1080, "1080p"), (720, "720p")]
AUTO_WATCH = [("off", "Off"), ("always", "On every site"), ("sites", "On sites chosen in the menu")]


class Settings(Gtk.Box):
    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        self.prefs = app.prefs
        head = Gtk.Box(spacing=16)
        head.add_css_class("sofa-page")
        self.back = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        self.back.connect("clicked", lambda _b: app.go_back_from_page())
        head.append(self.back)
        head.append(label("Settings", "sofa-page-title"))
        self.append(head)
        self.page = None
        self.holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.append(self.holder)

    def show(self):
        if self.page:
            self.holder.remove(self.page)
        self.page = self._build()
        self.page.set_vexpand(True)
        self.page.set_hexpand(True)
        self._widen(self.page, int(720 * float(self.prefs.ui_scale)))
        self.holder.append(self.page)
        self.back.grab_focus()

    def _widen(self, w, px):
        """Adw's page is sized for a phone-to-desktop range; across a room it can use more of the screen."""
        if isinstance(w, Adw.Clamp):
            w.set_unit(Adw.LengthUnit.PX)
            w.set_maximum_size(px)
            w.set_tightening_threshold(int(px * 0.8))
            return
        c = w.get_first_child()
        while c is not None:
            self._widen(c, px)
            c = c.get_next_sibling()

    # Row helpers bound to a preference
    def _switch(self, group, key, title, subtitle="", after=None):
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        row.set_active(bool(getattr(self.prefs, key)))

        def changed(r, _p):
            setattr(self.prefs, key, r.get_active())
            if after:
                after()

        row.connect("notify::active", changed)
        group.add(row)
        return row

    def _combo(self, group, key, title, choices, subtitle="", after=None):
        row = Adw.ComboRow(title=title, subtitle=subtitle)
        row.set_model(Gtk.StringList.new([c[1] for c in choices]))
        values = [c[0] for c in choices]
        cur = getattr(self.prefs, key)
        row.set_selected(values.index(cur) if cur in values else 0)

        def changed(r, _p):
            i = r.get_selected()
            if 0 <= i < len(values):
                setattr(self.prefs, key, values[i])
                if after:
                    after()

        row.connect("notify::selected", changed)
        group.add(row)
        return row

    def _entry(self, group, key, title, after=None):
        row = Adw.EntryRow(title=title)
        row.set_text(getattr(self.prefs, key) or "")
        row.set_show_apply_button(True)

        def applied(r):
            setattr(self.prefs, key, r.get_text().strip())
            if after:
                after()

        row.connect("apply", applied)
        group.add(row)
        return row

    def _button(self, group, title, subtitle, text, cb, destructive=False):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        b = Gtk.Button(label=text, valign=Gtk.Align.CENTER)
        if destructive:
            b.add_css_class("destructive-action")
        b.connect("clicked", lambda _b: cb())
        row.add_suffix(b)
        row.set_activatable_widget(b)
        group.add(row)
        return row

    def _build(self):
        a, p = self.app, self.prefs
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup(title="Appearance")
        self._combo(g, "theme", "Theme", THEMES, "Websites follow it too", after=a.apply_theme)
        self._combo(g, "ui_scale", "Interface size", SCALES, after=a.apply_theme)
        self._combo(g, "mouse_size", "Pointer size", MOUSE_SIZES, "The mouse pointer and the remote's dot",
                    after=a.apply_mouse_size)
        self._switch(g, "start_fullscreen", "Start in full screen", "F11 switches at any time")
        page.add(g)

        g = Adw.PreferencesGroup(title="Search")
        choices = [(e.id, e.name + (" (paid)" if e.paid else "")) for e in ENGINES] + [("custom", "Custom")]
        custom = None

        def engine_changed():
            custom.set_visible(p.search_engine == "custom")

        self._combo(g, "search_engine", "Search engine", choices, after=engine_changed)
        custom = self._entry(g, "custom_search", "Custom search address (%s is the search)")
        custom.set_visible(p.search_engine == "custom")
        self._switch(g, "search_suggestions", "Search suggestions",
                     "Sends what you type to the search engine as you type it. Never in private browsing")
        page.add(g)

        ab = a.adblock
        state = "Loading the filter lists…" if ab.loading else \
            f"{ab.rule_count:,} rules from {len(ab.lists())} lists, all matched on this computer."
        if ab.failed:
            state += " Not loaded: " + ", ".join(ab.failed) + "."
        g = Adw.PreferencesGroup(title="Ad blocking", description=state)
        self._switch(g, "adblock", "Block ads and trackers", after=a.adblock_changed)
        self._switch(g, "youtube_ads", "Take the ads out of YouTube's videos",
                     "Removes the ad entries from YouTube's player data. YouTube may change or notice this")
        self._switch(g, "auto_update_lists", "Update filter lists by themselves", "Every few days")
        self._button(g, "Update filter lists now", "Downloads the lists that come from the web", "Update",
                     lambda: (a.adblock.load(refresh=True), a.toast("Updating filter lists…")))
        if p.adblock_off_sites:
            self._button(g, "Sites with ad blocking off", ", ".join(p.adblock_off_sites[:6]) +
                         ("…" if len(p.adblock_off_sites) > 6 else ""), "Turn back on",
                         lambda: (setattr(p, "adblock_off_sites", []), a.adblock_changed(), self.show()))
        if p.autoclean_sites:
            self._button(g, "Sites cleaned up by themselves", ", ".join(p.autoclean_sites[:6]) +
                         ("…" if len(p.autoclean_sites) > 6 else ""), "Stop",
                         lambda: (setattr(p, "autoclean_sites", []), self.show()))
        if p.zaps:
            n = sum(len(v) for v in p.zaps.values())
            self._button(g, f"{n} removed element{'s' if n != 1 else ''}", ", ".join(list(p.zaps)[:6]) +
                         ("…" if len(p.zaps) > 6 else ""), "Bring back",
                         lambda: (a.adblock.clear_zaps(), a.toast("Removed elements are back"), self.show()))
        page.add(g)

        g = Adw.PreferencesGroup(
            title="Filter lists",
            description="Lists other than the two that come with Sofa are downloaded from their publishers when you turn "
                        "them on. More lists block more, and the first start after a change takes longer.")
        for f in CATALOG:
            sub = f.about
            if not ab.is_downloaded(f) and f.id in p.lists_on:
                sub += " · not downloaded yet"
            row = Adw.SwitchRow(title=f.title, subtitle=sub)
            row.set_active(f.id in p.lists_on)
            row.connect("notify::active", lambda r, _p, i=f.id: (a.adblock.set_list(i, r.get_active()),
                                                                 a.toast("Loading the filter lists…")))
            g.add(row)
        page.add(g)

        g = Adw.PreferencesGroup(title="Your own filter lists",
                                 description="Any list by its address: hosts files, Adblock Plus or uBlock Origin format.")
        for item in p.custom_lists:
            row = Adw.SwitchRow(title=item["url"])
            row.set_active(item.get("enabled", True))
            row.connect("notify::active", lambda r, _p, u=item["url"]: a.adblock.set_custom_enabled(u, r.get_active()))
            rm = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            rm.set_valign(Gtk.Align.CENTER)
            rm.add_css_class("flat")
            rm.connect("clicked", lambda _b, u=item["url"]: (a.adblock.remove_custom(u), self.show()))
            row.add_suffix(rm)
            g.add(row)
        add = Adw.EntryRow(title="Add a list by its address (https://…)")
        add.set_show_apply_button(True)

        def add_list(r):
            url = r.get_text().strip()
            if url.startswith("https://"):
                a.adblock.add_custom(url)
                a.toast("Downloading the list…")
                self.show()
            elif url.startswith("http://"):
                # Over plain HTTP, anyone on the way could change what the list allows or blocks.
                a.toast("A list needs an https:// address, so it can't be changed on the way", timeout=5)

        add.connect("apply", add_list)
        g.add(add)
        page.add(g)

        g = Adw.PreferencesGroup(title="Video",
                                 description="Watch and fullscreen videos play in mpv, with yt-dlp finding the stream.")
        have = player.available(p.player_command)
        if not have:
            g.add(Adw.ActionRow(title="mpv is not installed", subtitle="Install it with: sudo apt install mpv"))
        self._switch(g, "use_player", "Play videos in mpv",
                     "Watch opens the page's video in mpv. Off: it fills the window in the site's player")
        self._switch(g, "open_in_player", "Open videos in mpv when you start them",
                     "Only videos you start, with sound, a minute or longer")
        self._combo(g, "player_max_height", "Highest quality", QUALITIES)
        self._combo(g, "auto_watch", "Watch by itself", AUTO_WATCH, "Runs Watch once a page has loaded")
        if p.auto_watch_sites:
            self._button(g, "Sites chosen for Watch by itself", ", ".join(p.auto_watch_sites[:6]), "Forget",
                         lambda: (setattr(p, "auto_watch_sites", []), self.show()))
        self._switch(g, "autoplay", "Let pages start videos by themselves", after=a.browser.reconfigure)
        self._entry(g, "player_command", "Player command (mpv, or another player; {url} places the address)")
        self._entry(g, "player_extra_args", "Extra mpv options (for example --vo=gpu-next)")
        page.add(g)

        g = Adw.PreferencesGroup(title="Privacy")
        self._switch(g, "cookies", "Cookies", after=a.browser.reconfigure)
        self._switch(g, "block_3p_cookies", "Block third-party cookies", after=a.browser.reconfigure)
        self._switch(g, "send_gpc", "Send Global Privacy Control", "Few browsers send it, so it makes you stand out",
                     after=a.rebuild_view)
        self._switch(g, "javascript", "JavaScript", after=a.browser.reconfigure)
        self._combo(g, "history_days", "Keep history for", HISTORY_CHOICES,
                    after=lambda: a.db.trim_history(p.history_days))
        self._switch(g, "most_visited", "Show most visited sites on Home")
        self._switch(g, "clear_on_exit", "Clear browsing data on exit", "Cookies, site data, cache and history")
        self._button(g, "Clear browsing data", "Cookies, site data, cache and history", "Clear", a.clear_browsing_data,
                     destructive=True)
        page.add(g)

        g = Adw.PreferencesGroup(title="Browsing")
        self._combo(g, "user_agent", "Browse as", list(UA_LABELS.items()), "Desktop suits a big screen best",
                    after=a.browser.reconfigure)
        self._entry(g, "custom_user_agent", "Custom user agent", after=a.browser.reconfigure)
        self._switch(g, "back_goes_back", "Back goes to the previous page",
                     "Instead of opening the menu (Menu opens it then)")
        self._combo(g, "cursor_speed", "Pointer speed", SPEEDS, after=a.apply_pointer_speed)
        self._combo(g, "zoom", "Page zoom", ZOOMS, after=a.browser.reconfigure)
        self._switch(g, "restore_tabs", "Reopen tabs at start", "Not private tabs, and not after clearing on exit")
        self._switch(g, "unlimited_tabs", "No tab limit",
                     "More than six tabs, as long as there is memory for another page and some to spare",
                     after=a._update_menu)
        page.add(g)

        g = Adw.PreferencesGroup(
            title="Phone remote",
            description="Your phone as a trackpad, keyboard and remote, from a page this computer serves on your home "
                        "network. Nothing to install: scan the QR code. It only answers phones on the same network, and "
                        "only while Sofa is on screen.")
        self._switch(g, "phone_remote", "Phone remote", "Turned on by Pair phone, too", after=a.phone_changed)
        self._button(g, "Pair phone", "Shows the QR code to scan", "Show", a.show_pairing)
        self._button(g, "Forget paired phones", "A new QR code: phones paired before stop working", "Forget",
                     a.forget_phones)
        self._switch(g, "phone_plain_http", "Plain HTTP (less safe)",
                     "Only for a phone or Shortcut that won't accept Sofa's certificate. Someone watching your "
                     "network's traffic could read what you type with it", after=lambda: (a.phone_security_changed(),
                                                                                         self.show()))
        if p.phone_plain_http:
            g.add(Adw.ActionRow(title="It is plain HTTP on your home network",
                                subtitle="If you use a firewall: sudo ufw allow 8765/tcp"))
        else:
            g.add(Adw.ActionRow(title="A secure connection, with Sofa's own certificate",
                                subtitle="The phone warns once that the connection isn't private: no authority vouches "
                                         "for the certificate, so that's expected. If it warns again after that, "
                                         "Sofa made a new certificate, which it does every two years. "
                                         "If you use a firewall: sudo ufw allow 8765/tcp"))
        page.add(g)

        g = Adw.PreferencesGroup(title="Remote, keyboard and game pad")
        keys = [
            ("Arrows", "Move the pointer; push against an edge to scroll"),
            ("Enter / OK", "Click; hold still for a link's menu or scroll mode; hold and move to drag"),
            ("Escape / Back", "Menu (or the previous page, if set above)"),
            ("Menu / F1", "Menu"),
            ("Play / F2", "Watch: the page's main video, fullscreen in mpv"),
            ("Page Up / Down", "Scroll a page"),
            ("Home key on the remote / Alt+Home", "Home"),
            ("Search key / Ctrl+L / F6", "Search or type an address"),
            ("Ctrl+F, Ctrl+D, Ctrl+± , F5, F11", "Find, bookmark, zoom, reload, full screen"),
            ("Ctrl+T, Ctrl+W, Ctrl+Tab", "New tab, close tab, next tab"),
            ("Game pad", "Left stick aims, right stick scrolls, A select, B back, X Watch, Y search, "
                         "Start menu, Select Home, L1/R1 history, L2/R2 page"),
        ]
        if not a.gamepads.available:
            keys.append(("Game pads", "Install gir1.2-manette-0.2 for game pad support"))
        for k, v in keys:
            g.add(Adw.ActionRow(title=k, subtitle=v))
        page.add(g)

        g = Adw.PreferencesGroup(title="About")
        g.add(Adw.ActionRow(title=f"Sofa TV Browser for Linux {__version__}",
                            subtitle="A port of Sofa TV Browser for Android TV. GNU GPL v3.0 only."))
        page.add(g)
        return page


class Setup(Gtk.ScrolledWindow):
    """First start: the search engine, and whether to block more and keep the lists up to date."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.add_css_class("sofa-page")
        box.set_halign(Gtk.Align.CENTER)
        box.set_size_request(900, -1)
        box.append(label("Welcome to Sofa", "sofa-page-title"))
        box.append(label("Choose a search engine. You can change it later in Settings.", "sofa-hint", wrap=True))
        self.list = Gtk.ListBox()
        self.list.add_css_class("boxed-list")
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.choice = app.prefs.search_engine
        self.checks = {}
        group = None
        for e in ENGINES:
            row = Adw.ActionRow(title=e.name + (" · paid" if e.paid else ""), subtitle=e.tagline)
            check = Gtk.CheckButton()
            if group:
                check.set_group(group)
            group = group or check
            check.set_active(e.id == self.choice)
            check.connect("toggled", lambda c, i=e.id: c.get_active() and setattr(self, "choice", i))
            row.add_prefix(check)
            row.set_activatable_widget(check)
            self.list.append(row)
            self.checks[e.id] = check
        box.append(self.list)
        opts = Adw.PreferencesGroup()
        self.stronger = Adw.SwitchRow(title="Stronger ad blocking",
                                      subtitle="Adds EasyList and EasyPrivacy, downloaded from easylist.to. "
                                               "Settings has more lists")
        self.auto = Adw.SwitchRow(title="Update filter lists by themselves", subtitle="Every few days")
        opts.add(self.stronger)
        opts.add(self.auto)
        box.append(opts)
        go = Gtk.Button(label="Start")
        go.add_css_class("suggested-action")
        go.add_css_class("pill")
        go.set_halign(Gtk.Align.CENTER)
        go.connect("clicked", lambda _b: self._done())
        box.append(go)
        self.set_child(box)

    def focus(self):
        self.checks.get(self.choice, next(iter(self.checks.values()))).grab_focus()

    def _done(self):
        p = self.app.prefs
        p.search_engine = self.choice
        if self.stronger.get_active():
            p.lists_on = list(dict.fromkeys(list(p.lists_on) + STRONGER_IDS))
        p.stronger_asked = True
        p.auto_update_lists = self.auto.get_active()
        p.list_updates_asked = True
        p.setup_done = True
        if self.stronger.get_active():
            self.app.adblock.load()
        self.app.show_home()
