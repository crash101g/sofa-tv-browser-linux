# Sofa TV Browser for Linux - settings
# SPDX-License-Identifier: GPL-3.0-only
"""User settings, kept as JSON in ~/.config/sofa-tv-browser. Every default favours privacy."""

import json
import os
from dataclasses import dataclass
from urllib.parse import quote

from . import paths


@dataclass(frozen=True)
class SearchEngine:
    id: str
    name: str
    template: str
    suggest: str | None = None
    tagline: str = ""
    paid: bool = False

    def search_url(self, query: str) -> str:
        return self.template.replace("%s", quote(query, safe=""))

    def suggest_url(self, query: str) -> str | None:
        return self.suggest.replace("%s", quote(query, safe="")) if self.suggest else None


# The first-start choices, as in Sofa for Android (templates from each engine's OpenSearch description).
ENGINES = [
    SearchEngine("brave", "Brave Search", "https://search.brave.com/search?q=%s",
                 "https://search.brave.com/api/suggest?q=%s", "Its own independent index"),
    SearchEngine("duckduckgo", "DuckDuckGo", "https://duckduckgo.com/?q=%s",
                 "https://duckduckgo.com/ac/?type=list&q=%s", "Private search, results from Bing and others"),
    SearchEngine("ecosia", "Ecosia", "https://www.ecosia.org/search?q=%s",
                 "https://ac.ecosia.org/autocomplete?type=list&q=%s", "Plants trees with its profits"),
    SearchEngine("google", "Google", "https://www.google.com/search?q=%s",
                 "https://www.google.com/complete/search?client=firefox&q=%s", "The largest index"),
    SearchEngine("kagi", "Kagi", "https://kagi.com/search?q=%s",
                 "https://kagisuggest.com/api/autosuggest?q=%s", "Ad-free, needs an account", paid=True),
    SearchEngine("metager", "MetaGer", "https://metager.org/meta/meta.ger3?eingabe=%s", None,
                 "Non-profit meta search, needs a key", paid=True),
    SearchEngine("mojeek", "Mojeek", "https://www.mojeek.com/search?q=%s", None, "Its own index, no tracking"),
    SearchEngine("murena", "Murena Find", "https://murena.qwant.com/?q=%s",
                 "https://api.qwant.com/v3/suggest/?client=opensearch&q=%s", "Qwant results, by /e/OS"),
    SearchEngine("qwant", "Qwant", "https://www.qwant.com/?q=%s",
                 "https://api.qwant.com/v3/suggest/?client=opensearch&q=%s", "European, no tracking"),
    SearchEngine("startpage", "Startpage", "https://www.startpage.com/sp/search?query=%s",
                 "https://www.startpage.com/osuggestions?q=%s", "Google results, privately"),
    SearchEngine("swisscows", "Swisscows", "https://swisscows.com/web?query=%s", None, "Family-friendly, Swiss"),
    SearchEngine("uruky", "Uruky", "https://uruky.com/search?q=%s", None, "European, needs an account", paid=True),
]

CHROME_MAJOR = "142"
USER_AGENTS = {
    # Desktop Chrome on Windows: the most common browser, so the least distinctive.
    "desktop": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
               f"Chrome/{CHROME_MAJOR}.0.0.0 Safari/537.36",
    "mobile": f"Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
              f"Chrome/{CHROME_MAJOR}.0.0.0 Mobile Safari/537.36",
    "ipad": "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/18.0 Mobile/15E148 Safari/604.1",
    "tv": "Mozilla/5.0 (SMART-TV; LINUX; Tizen 8.0) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Version/8.0 TV Safari/537.36",
    "webkit": None,  # WebKitGTK's own
    "custom": None,
}
UA_LABELS = {"desktop": "Desktop", "mobile": "Mobile", "ipad": "Tablet", "tv": "TV", "webkit": "WebKit",
             "custom": "Custom"}

HISTORY_CHOICES = [(7, "7 days"), (30, "30 days"), (90, "90 days"), (-1, "Always"), (0, "Never")]

DEFAULTS = {
    "setup_done": False,
    "theme": "dark",                    # dark, light, system
    "ui_scale": 1.5,                    # interface size, for a TV across the room
    "start_fullscreen": True,
    "search_engine": "duckduckgo",
    "custom_search": "https://searx.example/search?q=%s",
    "search_suggestions": False,        # off: it sends every keystroke to the engine
    "adblock": True,
    "stronger_adblock": False,          # EasyList + EasyPrivacy
    "stronger_asked": False,
    "auto_update_lists": False,
    "list_updates_asked": False,
    "custom_lists": [],                 # [{"url": ..., "enabled": True}]
    "adblock_off_sites": [],
    "cookies": True,
    "block_3p_cookies": True,
    "send_gpc": False,
    "user_agent": "desktop",
    "custom_user_agent": "",
    "back_goes_back": False,
    "javascript": True,
    "autoplay": True,
    "open_in_player": True,             # a video started on a page with sound goes to mpv
    "use_player": True,                 # Watch plays in mpv ("Sofa Player")
    "player_command": "mpv",
    "player_extra_args": "",
    "player_max_height": 0,             # 0 = the screen's height
    "zoom": 100,
    "cursor_speed": 2,                  # 1 slow .. 3 fast
    "mouse_size": 48,                   # the mouse pointer, in pixels; 0 = the desktop's size
    "history_days": 90,
    "clear_on_exit": False,
    "most_visited": True,
    "private_mode": False,
    "keys_to_page": [],
    "pointer_off_sites": [],
    "auto_watch": "off",                # off, always, sites
    "auto_watch_sites": [],
    "last_list_update": 0,
    "lists_on": ["hosts", "popups"],    # filter lists in use, by id (see adblock.CATALOG)
    "zaps": {},                         # site -> CSS selectors of elements the user removed
    "autoclean_sites": [],              # sites cleaned up (overlays, cookie walls) on every load
    "youtube_ads": True,                # take the ad entries out of YouTube's player data
    "tabs": [],                         # the open tabs, reopened at the next start: [{"url", "title"}]
    "current_tab": 0,
    "restore_tabs": True,
    "unlimited_tabs": False,            # more than six tabs, while memory allows another
    "sleep_tabs_after": 30,             # minutes unused before a tab's page is closed to save memory; 0: never
    "phone_remote": False,              # the phone page's server; off until "Pair phone"
    "phone_plain_http": False,          # the phone page over plain HTTP, for phones that refuse Sofa's certificate
}


class Prefs:
    def __init__(self, path: str | None = None):
        self._path = path or os.path.join(paths.config_dir(), "settings.json")
        self._d = dict(DEFAULTS)
        saved = {}
        try:
            with open(self._path, encoding="utf-8") as f:
                saved = json.load(f)
            self._d.update(saved)
        except (OSError, ValueError):
            pass
        if "lists_on" not in saved and saved.get("stronger_adblock"):
            # Settings from before the list chooser: "Stronger ad blocking" meant EasyList and EasyPrivacy.
            self._d["lists_on"] = ["hosts", "popups", "easylist", "easyprivacy"]
        self._listeners = []

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        try:
            return self._d[key]
        except KeyError:
            raise AttributeError(key) from None

    def __setattr__(self, key, value):
        if key.startswith("_"):
            object.__setattr__(self, key, value)
            return
        if key not in DEFAULTS:
            raise AttributeError(key)
        if self._d.get(key) == value:
            return
        self._d[key] = value
        self.save()
        for cb in list(self._listeners):
            cb(key)

    def connect(self, cb):
        self._listeners.append(cb)

    def save(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._d, f, indent=1, sort_keys=True)
        os.replace(tmp, self._path)

    # Per-site sets.
    def in_set(self, key: str, item: str | None) -> bool:
        return item is not None and item in self._d.get(key, [])

    def set_in(self, key: str, item: str, on: bool):
        s = list(self._d.get(key, []))
        if on and item not in s:
            s.append(item)
        elif not on and item in s:
            s.remove(item)
        setattr(self, key, s)

    @property
    def engine(self) -> SearchEngine:
        if self.search_engine == "custom":
            return SearchEngine("custom", "Custom", self.custom_search)
        return next((e for e in ENGINES if e.id == self.search_engine), ENGINES[1])

    def user_agent_string(self) -> str | None:
        if self.user_agent == "custom":
            return self.custom_user_agent.strip() or None
        return USER_AGENTS.get(self.user_agent, USER_AGENTS["desktop"])
