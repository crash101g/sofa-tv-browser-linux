# SPDX-License-Identifier: GPL-3.0-only
"""
Ad blocking with filter lists, all matched on this computer.

Each list (hosts files, Adblock Plus and uBlock Origin syntax) is turned into WebKit content-blocker rules, which
WebKit compiles once and keeps; requests are then blocked inside the web engine, and element hiding happens there
too, also for elements a page adds later. The domains of every list are kept here as well, so that pop-ups to ad
sites can be stopped before they open (see Browser._on_create).

WebKit applies an exception rule only to the rules before it in the same list, so the exceptions of every list in
use (and the captcha providers, which are never blocked) are added to the end of each list. A list too large for
one WebKit filter is split, and each part ends with the same exceptions.

Elements the user removes ("Remove element") are kept per site as CSS selectors, and YouTube's player data loses
its ad entries through a small script in the page's own world, as uBlock Origin's json-prune does.
"""

import gzip
import hashlib
import json
import os
import re
import threading
import time
import urllib.request
from dataclasses import dataclass, field

import gi

gi.require_version("WebKit", "6.0")
from gi.repository import GLib, WebKit  # noqa: E402

from . import paths  # noqa: E402

UBO = "https://ublockorigin.github.io/uAssets/filters/"
ADGUARD = "https://filters.adtidy.org/extension/ublock/filters/"


@dataclass(frozen=True)
class FilterList:
    id: str
    title: str
    source: str          # a bundled file in sofa/data, or the list's address
    about: str = ""

    @property
    def bundled(self):
        return not self.source.startswith(("http://", "https://"))


# The lists Sofa knows by name. The two bundled lists are on at first; the others are downloaded once turned on.
CATALOG = [
    FilterList("hosts", "StevenBlack hosts", "blocklist-hosts.txt.gz", "Ads, trackers and malware. Comes with Sofa"),
    FilterList("popups", "HaGeZi Pop-Up Ads", "blocklist-popups.txt.gz", "Pop-up and pop-under ad sites. Comes with Sofa"),
    FilterList("easylist", "EasyList", "https://easylist.to/easylist/easylist.txt", "Ads, with element hiding"),
    FilterList("easyprivacy", "EasyPrivacy", "https://easylist.to/easylist/easyprivacy.txt", "Trackers"),
    FilterList("ubo-filters", "uBlock filters", UBO + "filters.txt", "uBlock Origin's own ad rules"),
    FilterList("ubo-privacy", "uBlock filters: Privacy", UBO + "privacy.txt", "More trackers"),
    FilterList("ubo-badware", "uBlock filters: Badware risks", UBO + "badware.txt", "Scam and fake-download sites"),
    FilterList("ubo-quick-fixes", "uBlock filters: Quick fixes", UBO + "quick-fixes.txt", "Recent fixes"),
    FilterList("ubo-unbreak", "uBlock filters: Unbreak", UBO + "unbreak.txt", "Fixes sites the other lists break"),
    FilterList("adguard-popups", "AdGuard Popups", ADGUARD + "19.txt",
               "Pop-up boxes on pages: newsletters, surveys, notification requests"),
    FilterList("peter-lowe", "Peter Lowe's ad and tracking servers",
               "https://pgl.yoyo.org/adservers/serverlist.php?hostformat=adblockplus&showintro=0&mimetype=plaintext",
               "A short, careful list"),
    FilterList("ubo-cookies", "uBlock filters: Cookie notices", UBO + "annoyances-cookies.txt", "Hides cookie banners"),
    FilterList("ubo-annoyances", "uBlock filters: Other annoyances", UBO + "annoyances-others.txt",
               "Newsletter boxes, chat bubbles and the like"),
    FilterList("adguard-mobile", "AdGuard Mobile Ads", ADGUARD + "11.txt", "Overlaps EasyList"),
    FilterList("adguard-annoyances", "AdGuard Other Annoyances", ADGUARD + "21.txt", "Large"),
    FilterList("fanboy-annoyance", "Fanboy's Annoyance", "https://secure.fanboy.co.nz/fanboy-annoyance.txt",
               "Very large: cookie notices, social buttons, chat"),
]
CATALOG_IDS = {f.id: f for f in CATALOG}
STRONGER_IDS = ["easylist", "easyprivacy"]

UPDATE_EVERY_S = 4 * 86400
# A rule format version: bump it to recompile every list after a change to the conversion.
FORMAT = "4"
# Rules per WebKit filter: larger lists are split.
CHUNK = 50000

# Captcha and bot-check providers are never blocked: a blocked captcha can't be solved, and the site behind it stays
# locked.
CHALLENGE_HOSTS = ["challenges.cloudflare.com", "hcaptcha.com", "recaptcha.net", "arkoselabs.com", "funcaptcha.com"]

_HOST_RE = re.compile(r"^[a-z0-9_-]+(\.[a-z0-9_-]+)+$")
_HOSTS_LINE = re.compile(r"^(?:0\.0\.0\.0|127\.0\.0\.1|::1?|::)\s+(\S+)")

TYPES = {
    "script": "script", "image": "image", "stylesheet": "style-sheet", "font": "font", "media": "media",
    "xmlhttprequest": "raw", "subdocument": "document", "document": "document", "popup": "popup",
    "ping": "ping", "websocket": "websocket", "other": "other", "object": "other",
}
ALL_TYPES = sorted(set(TYPES.values()) - {"popup"})
SKIP_OPTIONS = {"csp", "redirect", "redirect-rule", "rewrite", "removeparam", "queryprune", "replace", "badfilter",
                "header", "permissions", "urltransform", "uritransform", "method", "to", "denyallow", "generichide",
                "elemhide", "genericblock", "specifichide", "inline-script", "inline-font", "empty", "mp4",
                "cookie", "stealth", "jsonprune", "content", "extension", "webrtc"}
COSMETIC_UNSUPPORTED = (":has(", ":-abp-", ":xpath", ":style(", ":matches-", ":upward", ":remove", ":watch-attr",
                        ":min-text-length", ":others", ":matches-path", ":if", ":contains", ":nth-ancestor",
                        ":spath", ":not(:", "[-ext-", ":matches-attr", ":matches-prop")


def read_domains(text: str) -> set[str]:
    """Domains of a hosts file, a plain domain list, or the ||domain^ rules of an Adblock list."""
    out = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#![":
            continue
        m = _HOSTS_LINE.match(line)
        if m:
            line = m.group(1)
        elif line.startswith("||") and line.endswith("^") and "$" not in line:
            line = line[2:-1]
        line = line.split("#", 1)[0].strip().lower().rstrip(".")
        if line in ("localhost", "local", "broadcasthost", "0.0.0.0", "localhost.localdomain"):
            continue
        if _HOST_RE.match(line):
            out.add(line)
    return out


def is_adblock_syntax(text: str) -> bool:
    head = text[:20000]
    return "[Adblock" in head or "##" in head or "\n||" in head or head.startswith("||") or "\n! " in head


def _domain_regex(d: str) -> str:
    return r"^[a-z][a-z0-9.+-]*://([^/]*\.)?" + re.escape(d).replace("\\-", "-") + "[:/]"


def _url_regex(pattern: str) -> str | None:
    """An Adblock URL pattern as WebKit's restricted regex (no alternation, no {n}), or None."""
    p = pattern
    prefix = ""
    if p.startswith("||"):
        prefix = r"^[a-z][a-z0-9.+-]*://([^/]*\.)?"
        p = p[2:]
    elif p.startswith("|"):
        prefix = "^"
        p = p[1:]
    suffix = ""
    if p.endswith("|"):
        suffix = "$"
        p = p[:-1]
    out = []
    for ch in p:
        if ch == "*":
            out.append(".*")
        elif ch == "^":
            out.append(r"[^a-zA-Z0-9_.%-]")
        elif ch in ".?+()[]{}\\$|":
            out.append("\\" + ch)
        else:
            out.append(ch)
    body = "".join(out)
    while body.startswith(".*"):
        body = body[2:]
    while body.endswith(".*"):
        body = body[:-2]
    if len(body.replace("\\", "")) < 3 and not prefix:
        return None
    if not body.isascii():
        return None
    return prefix + body + suffix


def _ascii_domain(d: str) -> str | None:
    """WebKit takes lower-case ASCII domains only: others as punycode, or None."""
    if d.isascii():
        return d
    try:
        return d.encode("idna").decode("ascii")
    except UnicodeError:
        return None


def _domains(spec: str, sep: str):
    inc, exc = [], []
    for d in spec.split(sep):
        d = d.strip().lower()
        neg = d.startswith("~")
        d = _ascii_domain(d[1:] if neg else d)
        if not d or "*" in d or "/" in d or not _HOST_RE.match(d):
            continue
        d = "~" + d if neg else d
        if d.startswith("~"):
            exc.append("*" + d[1:])
        else:
            inc.append("*" + d)
    return inc, exc


def _network_rule(line: str):
    exception = line.startswith("@@")
    if exception:
        line = line[2:]
    if line.startswith("/") and (line.endswith("/") or "/$" in line) and len(line) > 2:
        return None  # full regular expressions use syntax WebKit lacks
    pattern, sep, opts = line.rpartition("$")
    if not sep or "/" in opts or "^" in opts or "*" in opts:
        pattern, opts = line, ""  # a "$" that is part of the address, not options
    trigger = {}
    action = {"type": "ignore-previous-rules" if exception else "block"}
    types, not_types = [], []
    document_exception = False
    for o in filter(None, (x.strip() for x in opts.split(","))):
        neg = o.startswith("~")
        name, _, value = (o[1:] if neg else o).partition("=")
        name = name.lower()
        if name in ("third-party", "3p"):
            trigger["load-type"] = ["first-party" if neg else "third-party"]
        elif name in ("first-party", "1p"):
            trigger["load-type"] = ["third-party" if neg else "first-party"]
        elif name == "domain" or name == "from":
            inc, exc = _domains(value, "|")
            if inc:
                trigger["if-domain"] = inc
            elif exc:
                trigger["unless-domain"] = exc
        elif name == "match-case":
            trigger["url-filter-is-case-sensitive"] = True
        elif name in ("important", "all", "strict3p", "strict1p", "_", "__"):
            pass
        elif name == "document" or name == "doc":
            if exception:
                document_exception = True
            else:
                (not_types if neg else types).append("document")
        elif name in ("xhr",):
            (not_types if neg else types).append("raw")
        elif name in ("css",):
            (not_types if neg else types).append("style-sheet")
        elif name in ("frame",):
            (not_types if neg else types).append("document")
        elif name in TYPES:
            (not_types if neg else types).append(TYPES[name])
        else:  # options WebKit can't express (SKIP_OPTIONS and anything unknown): leave the rule out
            return None
    if document_exception:
        dom = pattern.lstrip("|").split("^")[0].split("/")[0]
        if not _HOST_RE.match(dom or ""):
            return None
        return {"trigger": {"url-filter": ".*", "if-domain": ["*" + dom]}, "action": {"type": "ignore-previous-rules"}}
    regex = _url_regex(pattern)
    if regex is None:
        return None
    trigger["url-filter"] = regex
    if not_types and not types:
        types = [t for t in ALL_TYPES if t not in not_types]
    if types:
        trigger["resource-type"] = sorted(set(types))
    return {"trigger": trigger, "action": action}


def challenge_rules() -> list:
    """Exceptions for the captcha providers, added to every list."""
    out = [{"trigger": {"url-filter": _domain_regex(h)}, "action": {"type": "ignore-previous-rules"}}
           for h in CHALLENGE_HOSTS]
    for h in ("google.com", "gstatic.com"):
        out.append({"trigger": {"url-filter": _domain_regex(h)[:-len("[:/]")] + "(:[0-9]+)?/recaptcha/"},
                    "action": {"type": "ignore-previous-rules"}})
    return out


@dataclass
class Parsed:
    """One list's rules: blocking and hiding rules, exceptions, and the domains it blocks outright or as pop-ups."""
    blocks: list = field(default_factory=list)
    exceptions: list = field(default_factory=list)
    domains: set = field(default_factory=set)
    popups: set = field(default_factory=set)


_POPUP_RULE = re.compile(r"^\|\|([a-z0-9.-]+)\^?\$(?:(?:third-party|3p|~first-party),)?popup(?:,(?:third-party|3p))?$")


def parse(text: str) -> Parsed:
    """A list's text as WebKit content-blocker rules."""
    p = Parsed(domains=read_domains(text))
    if not is_adblock_syntax(text):
        p.blocks = [{"trigger": {"url-filter": _domain_regex(d)}, "action": {"type": "block"}} for d in sorted(p.domains)]
        return p
    blocks, exceptions, generic_css = [], [], []
    specific_css: dict[tuple, list] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("!", "[")):
            continue
        if "#@#" in line or "#?#" in line or "#$#" in line or "#%#" in line or "$$" in line or "##+js" in line:
            continue
        if "##" in line:
            doms, _, sel = line.partition("##")
            if not sel or sel.startswith("^") or any(u in sel for u in COSMETIC_UNSUPPORTED):
                continue
            if doms:
                inc, exc = _domains(doms, ",")
                if inc:
                    specific_css.setdefault(("if-domain", tuple(sorted(inc))), []).append(sel)
                elif exc:
                    specific_css.setdefault(("unless-domain", tuple(sorted(exc))), []).append(sel)
            else:
                generic_css.append(sel)
            continue
        m = _HOSTS_LINE.match(line)
        if m or _HOST_RE.match(line.lower()):
            continue  # already in domains
        if line.startswith("||") and line.endswith("^") and "$" not in line:
            continue  # plain domain, already in domains
        m = _POPUP_RULE.match(line.lower())
        if m and _HOST_RE.match(m.group(1)):
            p.popups.add(m.group(1))
        rule = _network_rule(line)
        if rule:
            (exceptions if rule["action"]["type"] == "ignore-previous-rules" else blocks).append(rule)
    rules = [{"trigger": {"url-filter": _domain_regex(d)}, "action": {"type": "block"}} for d in sorted(p.domains)]
    rules += blocks
    for i in range(0, len(generic_css), 250):
        rules.append({"trigger": {"url-filter": ".*"},
                      "action": {"type": "css-display-none", "selector": ", ".join(generic_css[i:i + 250])}})
    for (kind, doms), sels in specific_css.items():
        rules.append({"trigger": {"url-filter": ".*", kind: list(doms)},
                      "action": {"type": "css-display-none", "selector": ", ".join(sels)}})
    p.blocks = rules
    p.exceptions = exceptions
    return p


def convert(text: str) -> tuple[list, set[str]]:
    """A list's text as one WebKit rule list (its exceptions last), and the plain domains it blocks."""
    p = parse(text)
    return p.blocks + p.exceptions, p.domains


def chunks(blocks: list, shared: list, size: int = CHUNK) -> list[list]:
    """[blocks] split into WebKit filters of at most [size] rules, each ending with the [shared] exceptions."""
    room = max(size - len(shared), 1000)
    parts = [blocks[i:i + room] for i in range(0, len(blocks), room)] or [[]]
    return [part + shared for part in parts]


# Elements the user removed: one style sheet per site, applied to that site's pages only.
_SELECTOR_BAD = re.compile(r"[{}<>;@\n]")
MAX_ZAPS = 500


def valid_selector(sel: str) -> bool:
    return bool(sel) and len(sel) <= 200 and bool(sel.strip()) and not _SELECTOR_BAD.search(sel)


def zap_sheets(zaps: dict) -> list:
    out = []
    for site, sels in zaps.items():
        sels = [x for x in sels if valid_selector(x)]
        if not sels or not re.match(r"^[a-z0-9.-]+$", site or ""):
            continue
        # One rule per selector, so a selector WebKit rejects doesn't take the others with it.
        css = "\n".join(f"{x} {{ display: none !important; }}" for x in sels)
        out.append(WebKit.UserStyleSheet.new(css, WebKit.UserContentInjectedFrames.TOP_FRAME, WebKit.UserStyleLevel.USER,
                                             [f"*://{site}/*", f"*://*.{site}/*"], None))
    return out


# YouTube: the ad fields leave the player's data before the player sees it (uBlock Origin's json-prune does the same).
YOUTUBE_SCRIPT = r"""(function () {
  const AD_KEYS = ['adPlacements', 'adSlots', 'playerAds', 'adBreakHeartbeatParams'];
  const strip = (o) => {
    if (o && typeof o === 'object') {
      for (const k of AD_KEYS) if (k in o) delete o[k];
      if (o.playerResponse) strip(o.playerResponse);
    }
    return o;
  };
  for (const name of ['ytInitialPlayerResponse', 'playerResponse']) {
    let v;
    try { Object.defineProperty(window, name, { configurable: true, get: () => v, set: (x) => { v = strip(x); } }); }
    catch (e) {}
  }
  const parse = JSON.parse;
  JSON.parse = function () { return strip(parse.apply(this, arguments)); };
  const json = Response.prototype.json;
  Response.prototype.json = function () { return json.apply(this, arguments).then(strip); };
})();"""
YOUTUBE_SITES = ["*://youtube.com/*", "*://*.youtube.com/*", "*://youtube-nocookie.com/*", "*://*.youtube-nocookie.com/*"]


def youtube_script():
    return WebKit.UserScript.new(YOUTUBE_SCRIPT, WebKit.UserContentInjectedFrames.ALL_FRAMES,
                                 WebKit.UserScriptInjectionTime.START, YOUTUBE_SITES, None)


class AdBlocker:
    """The filter lists, their compiled WebKit filters, and the domain sets for pop-ups."""

    def __init__(self, prefs):
        self.prefs = prefs
        self.dir = paths.sub(paths.data_dir(), "lists")
        self.store = WebKit.UserContentFilterStore.new(paths.sub(paths.cache_dir(), "content-filters"))
        self.filters: dict[str, WebKit.UserContentFilter] = {}
        self.domains: set[str] = set()
        self.popup_domains: set[str] = set()
        self.rule_count = 0
        self.failed: list[str] = []
        self._listeners = []
        self.loading = False
        self._again = False

    # The lists in use: FilterList entries, custom lists included.
    def lists(self):
        on = set(self.prefs.lists_on)
        out = [f for f in CATALOG if f.id in on]
        for item in self.prefs.custom_lists:
            if item.get("enabled", True):
                out.append(FilterList("custom-" + hashlib.sha1(item["url"].encode()).hexdigest()[:12], item["url"],
                                      item["url"]))
        return out

    def connect(self, cb):
        self._listeners.append(cb)

    def _changed(self):
        for cb in list(self._listeners):
            cb()

    def _under(self, host, domains):
        if not host:
            return False
        parts = host.lower().split(".")
        return any(".".join(parts[i:]) in domains for i in range(len(parts) - 1))

    def blocks_host(self, host: str | None) -> bool:
        return self._under(host, self.domains)

    def blocks_popup(self, host: str | None) -> bool:
        """A window to [host] is an ad: a listed domain, or one a `$popup` rule names."""
        return self._under(host, self.domains) or self._under(host, self.popup_domains)

    def is_downloaded(self, f: FilterList) -> bool:
        return f.bundled or os.path.exists(self._text_path(f.id))

    def _text_path(self, list_id):
        return os.path.join(self.dir, list_id + ".txt")

    def _read_text(self, f: FilterList) -> str | None:
        if f.bundled:
            with gzip.open(paths.resource("data", f.source), "rt", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        try:
            with open(self._text_path(f.id), encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return None

    def load(self, refresh=False):
        """Loads every list in use (downloading missing ones), compiling those not compiled yet. Off the main loop."""
        if self.loading:
            self._again = True
            return
        self.loading = True
        threading.Thread(target=self._load, args=(refresh,), daemon=True).start()

    def _load(self, refresh):
        wanted = self.lists()
        due = refresh or (self.prefs.auto_update_lists and time.time() - self.prefs.last_list_update > UPDATE_EVERY_S)
        failed = []
        for f in wanted:
            if not f.bundled and (due or not os.path.exists(self._text_path(f.id))):
                if not self._download(f.id, f.source) and not os.path.exists(self._text_path(f.id)):
                    failed.append(f.title)
        if due:
            GLib.idle_add(lambda: setattr(self.prefs, "last_list_update", int(time.time())) or False)
        parsed = []
        for f in wanted:
            text = self._read_text(f)
            if text is None:
                continue
            parsed.append((f.id, hashlib.sha1(text.encode()).hexdigest()[:16], parse(text)))
        # Every list's exceptions, at the end of every list (see the module notes).
        shared, seen = [], set()
        for _id, _h, p in parsed:
            for r in p.exceptions:
                key = json.dumps(r, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    shared.append(r)
        shared += challenge_rules()
        shared_hash = hashlib.sha1(json.dumps(shared, sort_keys=True).encode()).hexdigest()[:8]
        jobs = []
        for list_id, text_hash, p in parsed:
            for n, rules in enumerate(chunks(p.blocks, shared)):
                jobs.append((f"{list_id}#{n}", f"{list_id}-{FORMAT}-{text_hash}-{shared_hash}-{n}", rules))
        domains = set().union(*(p.domains for _i, _h, p in parsed)) if parsed else set()
        popups = set().union(*(p.popups for _i, _h, p in parsed)) if parsed else set()
        GLib.idle_add(self._compile_all, jobs, domains, popups, failed)

    def _download(self, list_id, url) -> bool:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Sofa TV Browser"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read(64 * 1024 * 1024)
            text = data.decode("utf-8", errors="replace")
            if "<html" in text[:2000].lower() or len(text) < 200:
                return False
            tmp = self._text_path(list_id) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self._text_path(list_id))
            return True
        except Exception as e:  # noqa: BLE001 - a list that can't be fetched keeps its last copy
            print(f"sofa: could not download {url}: {e}")
            return False

    def _compile_all(self, jobs, domains, popups, failed):
        self._new = {}
        self._new_count = 0
        self._new_failed = list(failed)
        self._pending = len(jobs)
        self._next = (domains, popups)
        if not jobs:
            self._finish()
        for key, ident, rules in jobs:
            self._compile(key, ident, rules)
        return False

    def _compile(self, key, ident, rules):
        def keep(f, n):
            self._new[key] = f
            self._new_count += n

        def saved(store, res):
            try:
                keep(store.save_finish(res), len(rules))
            except GLib.Error as e:
                # One bad rule fails the whole list: keep its network rules, which convert reliably.
                safe = [r for r in rules if r["action"]["type"] != "css-display-none"]
                if len(safe) < len(rules):
                    print(f"sofa: list {key}: {e.message}; retrying without element hiding")
                    self.store.save(ident + "-net", GLib.Bytes.new(json.dumps(safe).encode()), None,
                                    lambda st, r: saved_safe(st, r, len(safe)))
                    return
                print(f"sofa: list {key} could not be compiled: {e.message}")
                self._new_failed.append(key.split("#")[0])
            self._one_done()

        def saved_safe(store, res, n):
            try:
                keep(store.save_finish(res), n)
            except GLib.Error as e:
                print(f"sofa: list {key} could not be compiled: {e.message}")
                self._new_failed.append(key.split("#")[0])
            self._one_done()

        def loaded(store, res):
            try:
                keep(store.load_finish(res), len(rules))
                self._one_done()
            except GLib.Error:
                self.store.save(ident, GLib.Bytes.new(json.dumps(rules).encode()), None, saved)

        self.store.load(ident, None, loaded)

    def _one_done(self):
        self._pending -= 1
        if self._pending <= 0:
            self._finish()

    def _finish(self):
        self.filters = self._new
        self.rule_count = self._new_count
        self.failed = sorted(set(self._new_failed))
        self.domains, self.popup_domains = self._next
        self.loading = False
        self._prune_store()
        self._changed()
        if self._again:
            self._again = False
            self.load()

    def _prune_store(self):
        keep = {f.get_identifier() for f in self.filters.values()}

        def listed(store, res):
            try:
                ids = store.fetch_identifiers_finish(res) or []
            except GLib.Error:
                return
            for i in ids:
                if i not in keep:
                    store.remove(i, None, removed)

        def removed(store, res):
            try:
                store.remove_finish(res)
            except GLib.Error:
                pass

        self.store.fetch_identifiers(None, listed)

    def apply(self, ucm: WebKit.UserContentManager, on: bool):
        ucm.remove_all_filters()
        if on:
            for f in self.filters.values():
                ucm.add_filter(f)

    def set_list(self, list_id: str, on: bool):
        ids = [i for i in self.prefs.lists_on if i != list_id] + ([list_id] if on else [])
        self.prefs.lists_on = [f.id for f in CATALOG if f.id in ids]
        self.load()

    def add_custom(self, url: str):
        lists = [dict(x) for x in self.prefs.custom_lists if x["url"] != url]
        lists.append({"url": url, "enabled": True})
        self.prefs.custom_lists = lists
        self.load()

    def set_custom_enabled(self, url: str, on: bool):
        self.prefs.custom_lists = [dict(x, enabled=on) if x["url"] == url else dict(x) for x in self.prefs.custom_lists]
        self.load()

    def remove_custom(self, url: str):
        self.prefs.custom_lists = [dict(x) for x in self.prefs.custom_lists if x["url"] != url]
        self.load()

    # Removed elements, per site
    def zaps_for(self, site: str | None) -> list:
        return list(self.prefs.zaps.get(site or "", []))

    def add_zap(self, site: str, selector: str) -> bool:
        if not site or not valid_selector(selector):
            return False
        allz = {k: list(v) for k, v in self.prefs.zaps.items()}
        if sum(len(v) for v in allz.values()) >= MAX_ZAPS:
            return False
        if selector not in allz.setdefault(site, []):
            allz[site].append(selector)
        self.prefs.zaps = allz
        return True

    def clear_zaps(self, site: str | None = None):
        self.prefs.zaps = {} if site is None else {k: list(v) for k, v in self.prefs.zaps.items() if k != site}
