# SPDX-License-Identifier: GPL-3.0-only
"""Tests of Sofa's plain logic: run with  python3 -m unittest discover tests"""

import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json  # noqa: E402

from sofa import adblock, cursors, gamepad, phone, player  # noqa: E402
from sofa import browser  # noqa: E402
from sofa.browser import looks_like_url, to_url  # noqa: E402
from sofa.db import Db, host_of, pretty_host, site_of  # noqa: E402
from sofa.prefs import ENGINES, Prefs  # noqa: E402

DDG = next(e for e in ENGINES if e.id == "duckduckgo")


class UrlTest(unittest.TestCase):
    def test_addresses(self):
        for t in ("example.com", "www.example.co.uk/path?q=1", "https://x.org", "localhost:8080", "192.168.1.2:32400",
                  "about:blank"):
            self.assertTrue(looks_like_url(t), t)
        for t in ("cat videos", "hello", "what is 2.5", "example."):
            self.assertFalse(looks_like_url(t), t)

    def test_to_url(self):
        self.assertEqual(to_url("example.com", DDG), "https://example.com")
        self.assertEqual(to_url("localhost:8080/x", DDG), "http://localhost:8080/x")
        self.assertEqual(to_url("http://a.b/c", DDG), "http://a.b/c")
        self.assertEqual(to_url("cat videos", DDG), "https://duckduckgo.com/?q=cat%20videos")
        self.assertEqual(to_url("a&b=c", DDG), "https://duckduckgo.com/?q=a%26b%3Dc")

    def test_hosts(self):
        self.assertEqual(host_of("https://www.YouTube.com/watch"), "youtube.com")
        self.assertIsNone(host_of("about:blank"))
        self.assertEqual(pretty_host("news.example.co.uk"), "News Example")
        self.assertEqual(pretty_host("192.168.1.1"), "192.168.1.1")
        self.assertEqual(site_of("news.bbc.co.uk"), "bbc.co.uk")
        self.assertEqual(site_of("a.b.example.com"), "example.com")
        self.assertEqual(site_of("192.168.1.10"), "192.168.1.10")
        self.assertIsNone(site_of(None))



class TabLimitTest(unittest.TestCase):
    def meminfo(self, d, total, avail):
        path = os.path.join(d, "meminfo")
        with open(path, "w") as f:
            f.write(f"MemTotal:       {total} kB\nMemFree:        1000 kB\n")
            if avail is not None:
                f.write(f"MemAvailable:   {avail} kB\nBuffers:        10 kB\n")
        return path

    def test_memory(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(browser.memory(self.meminfo(d, 16000000, 4000000)), (16000000, 4000000))
            self.assertIsNone(browser.memory(self.meminfo(d, 16000000, None)))      # too old a kernel
            self.assertIsNone(browser.memory(os.path.join(d, "missing")))
        self.assertIsNotNone(browser.memory())                                      # this computer's

    def test_page_memory(self):
        with tempfile.TemporaryDirectory() as d:
            def proc(pid, ppid, name, pss=None, rss=None):
                os.makedirs(os.path.join(d, str(pid)))
                with open(os.path.join(d, str(pid), "stat"), "w") as f:
                    f.write(f"{pid} ({name}) S {ppid} 1 1\n")
                if pss is not None:
                    with open(os.path.join(d, str(pid), "smaps_rollup"), "w") as f:
                        f.write(f"Rss:  {pss * 2} kB\nPss:  {pss} kB\n")
                if rss is not None:
                    with open(os.path.join(d, str(pid), "status"), "w") as f:
                        f.write(f"Name: x\nVmRSS:  {rss} kB\n")
            self.assertIsNone(browser.page_memory(100, d))
            proc(100, 1, "python3")
            proc(101, 100, "bwrap")
            proc(102, 101, "WebKitWebProces", pss=200000)      # in the sandbox
            proc(103, 100, "WebKitWebProces", rss=50000)       # no smaps_rollup: resident size
            proc(104, 100, "WebKitNetworkPr", pss=90000)       # not a page
            proc(105, 1, "WebKitWebProces", pss=70000)         # another browser's
            self.assertEqual(browser.page_memory(100, d), 250000)

    def test_tab_need(self):
        gb = 1024 * 1024
        self.assertEqual(browser.tab_need(8 * gb, None, 0), 300 * 1024 + 512 * 1024)      # a guess, the 512 MB floor
        self.assertEqual(browser.tab_need(32 * gb, 2 * gb, 4), 512 * 1024 + 32 * gb // 20)  # 5% of a big computer
        self.assertEqual(browser.tab_need(8 * gb, 9 * gb, 3), gb + 512 * 1024)              # a page counts 1 GB at most
        self.assertEqual(browser.tab_need(8 * gb, 1000, 3), 100 * 1024 + 512 * 1024)        # and 100 MB at least

    def browser_with(self, tabs, unlimited):
        b = browser.Browser.__new__(browser.Browser)
        b.tabs = [object()] * tabs
        b.saved = None
        b.prefs = type("P", (), {"unlimited_tabs": unlimited})()
        return b

    def test_room(self):
        gb = 1024 * 1024
        real = browser.memory, browser.page_memory
        try:
            browser.page_memory = lambda: 2 * gb                                     # 2 GB of pages
            browser.memory = lambda: (8 * gb, 6 * gb)
            self.assertIsNone(self.browser_with(5, False).tab_room())
            self.assertEqual(self.browser_with(6, False).tab_room(), "6 tabs is the limit")
            self.assertIsNone(self.browser_with(40, True).tab_room())                # no limit, memory to spare
            browser.memory = lambda: (8 * gb, gb)                                    # 1 GB free
            self.assertIsNone(self.browser_with(10, True).tab_room())                # 200 MB pages: room
            msg = self.browser_with(2, True).tab_room()                              # 1 GB pages: not
            self.assertEqual(msg, "Only 1.0 GB of memory is free, and a new tab needs about 1.5 GB")
            self.assertIsNone(self.browser_with(5, False).tab_room())                 # the usual limit ignores it
            browser.memory = lambda: None
            self.assertIsNone(self.browser_with(5, True).tab_room())                  # can't tell: six
            self.assertEqual(self.browser_with(6, True).tab_room(), "6 tabs is the limit")
        finally:
            browser.memory, browser.page_memory = real

class AdBlockTest(unittest.TestCase):
    def test_hosts_file(self):
        d = adblock.read_domains("# c\n0.0.0.0 ads.example.com\n127.0.0.1 localhost\ntracker.net\n||pop.io^\n")
        self.assertEqual(d, {"ads.example.com", "tracker.net", "pop.io"})
        # Element hiding rules name the sites they hide things on, not sites to block.
        d = adblock.read_domains("news.com##.ad\nshop.com,b.org#@#.ad\nx.com#?#.ad\nads.net # comment\n")
        self.assertEqual(d, {"ads.net"})
        rules, doms = adblock.convert("0.0.0.0 ads.example.com\n")
        self.assertEqual(len(rules), 1)
        rx = re.compile(rules[0]["trigger"]["url-filter"])
        self.assertTrue(rx.search("https://ads.example.com/x.js"))
        self.assertTrue(rx.search("https://cdn.ads.example.com:443/x.js"))
        self.assertFalse(rx.search("https://ads.example.com.evil.org/"))
        self.assertFalse(rx.search("https://badads.example.com/"))

    def test_abp(self):
        text = "[Adblock Plus 2.0]\n! c\n||ads.com^$third-party\n/banner/*$image,domain=a.com|b.com\n" \
               "@@||good.com^$document\n##.ad-box\nsite.org##.promo\n||x.com^$csp=script-src\n" \
               "/^regex$/\n##div:has(> .ad)\n"
        rules, doms = adblock.convert(text)
        kinds = [r["action"]["type"] for r in rules]
        self.assertEqual(kinds[-1], "ignore-previous-rules")  # exceptions come last
        banner = next(r for r in rules if "banner" in r["trigger"].get("url-filter", ""))
        self.assertEqual(banner["trigger"]["resource-type"], ["image"])
        self.assertEqual(banner["trigger"]["if-domain"], ["*a.com", "*b.com"])
        third = next(r for r in rules if "ads" in r["trigger"].get("url-filter", ""))
        self.assertEqual(third["trigger"]["load-type"], ["third-party"])
        css = [r for r in rules if r["action"]["type"] == "css-display-none"]
        self.assertEqual({r["action"]["selector"] for r in css}, {".ad-box", ".promo"})
        self.assertFalse(any("x.com" in r["trigger"].get("url-filter", "") for r in rules))  # $csp left out
        self.assertFalse(any(":has(" in r["action"].get("selector", "") for r in rules))

    def test_url_regex(self):
        rx = re.compile(adblock._url_regex("||example.com/ads/*.js|"))
        self.assertTrue(rx.search("https://sub.example.com/ads/a.js"))
        self.assertFalse(rx.search("https://example.com/ads/a.json"))
        self.assertIsNone(adblock._url_regex("ab"))

    def test_bundled_lists_load(self):
        import gzip
        for f in adblock.CATALOG:
            if f.bundled:
                with gzip.open(os.path.join(os.path.dirname(adblock.__file__), "data", f.source), "rt") as fh:
                    self.assertGreater(len(adblock.read_domains(fh.read())), 10000)

    def test_catalog(self):
        ids = [f.id for f in adblock.CATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual([f.id for f in adblock.CATALOG if f.bundled], ["hosts", "popups"])
        for f in adblock.CATALOG:
            self.assertTrue(f.bundled or f.source.startswith("https://"), f.id)

    def test_popups_and_exceptions(self):
        p = adblock.parse("[Adblock Plus 2.0]\n||pop.example^$popup\n||pop2.example^$popup,third-party\n"
                          "||img.example^$image,popup\n@@||ok.example^\n||ads.example^\n")
        self.assertEqual(p.popups, {"pop.example", "pop2.example"})
        self.assertEqual(p.domains, {"ads.example"})
        self.assertTrue(all(r["action"]["type"] == "ignore-previous-rules" for r in p.exceptions))
        self.assertTrue(any("ok" in r["trigger"]["url-filter"] for r in p.exceptions))

    def test_challenges_never_blocked(self):
        rules = adblock.challenge_rules()
        self.assertTrue(all(r["action"]["type"] == "ignore-previous-rules" for r in rules))
        rx = [re.compile(r["trigger"]["url-filter"]) for r in rules]
        for url in ("https://challenges.cloudflare.com/turnstile/v0/api.js", "https://js.hcaptcha.com/1/api.js",
                    "https://www.google.com/recaptcha/api.js", "https://www.gstatic.com/recaptcha/releases/x.js"):
            self.assertTrue(any(r.search(url) for r in rx), url)
        self.assertFalse(any(r.search("https://www.google.com/search?q=recaptcha") for r in rx))

    def test_chunks_end_with_shared_exceptions(self):
        shared = [{"trigger": {"url-filter": "x"}, "action": {"type": "ignore-previous-rules"}}]
        blocks = [{"trigger": {"url-filter": str(i)}, "action": {"type": "block"}} for i in range(2500)]
        parts = adblock.chunks(blocks, shared, size=1001)
        self.assertEqual(sum(len(p) - 1 for p in parts), 2500)
        self.assertTrue(all(p[-1] is shared[0] and len(p) <= 1001 for p in parts))
        self.assertEqual(adblock.chunks([], shared), [shared])

    def test_non_ascii_domains(self):
        rules, _ = adblock.convert("[Adblock Plus 2.0]\nmünchen.de,~bücher.de##.ad\n")
        doms = [d for r in rules for k in ("if-domain", "unless-domain") for d in r["trigger"].get(k, [])]
        self.assertTrue(doms and all(d.isascii() for d in doms), doms)

    def test_zap_selectors(self):
        self.assertTrue(adblock.valid_selector("div.sponsor-box"))
        self.assertTrue(adblock.valid_selector("#\\31 23"))
        for bad in ("", "a{}", "a;b", "@import", "x" * 201, "a\nb", "</style>"):
            self.assertFalse(adblock.valid_selector(bad), bad)


    def test_lists_only_over_https(self):
        import urllib.request
        blocker = adblock.AdBlocker.__new__(adblock.AdBlocker)
        self.assertFalse(blocker._download("x", "http://lists.example/list.txt"))

        class Redirected:
            def geturl(self):
                return "http://lists.example/list.txt"

            def read(self, _n):
                raise AssertionError("read a list that came over plain HTTP")

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        real = urllib.request.urlopen
        urllib.request.urlopen = lambda *_a, **_k: Redirected()
        try:
            self.assertFalse(blocker._download("x", "https://lists.example/list.txt"))
        finally:
            urllib.request.urlopen = real

    def _page_rules(self, rules):
        """The rules that can block the page being opened (not only something in it, or a frame)."""
        return [r for r in rules if r["action"]["type"] == "block" and r["trigger"].get("load-context") != ["child-frame"]
                and "document" in r["trigger"].get("resource-type", ["document"])]

    def test_only_doc_and_site_rules_block_the_page_opened(self):
        # A rule for ad pictures must not block an article whose address matches it; it still blocks ad frames.
        rules = adblock._network_rule("/ad-")
        self.assertEqual(self._page_rules(rules), [])
        self.assertIn("image", rules[0]["trigger"]["resource-type"])
        self.assertEqual(rules[1]["trigger"], {"url-filter": "/ad-", "resource-type": ["document"],
                                               "load-context": ["child-frame"]})
        self.assertEqual(self._page_rules(adblock._network_rule("/banner/*$subdocument")), [])
        # $document, and a whole site, still block the page (with a choice to open it anyway: see Browser).
        self.assertEqual(len(self._page_rules(adblock._network_rule("/landing-$doc"))), 1)
        self.assertEqual(len(self._page_rules(adblock._network_rule("||ads.example^$third-party"))), 1)
        rules, _ = adblock.convert("[Adblock]\n||ads.example.com^\n")
        self.assertEqual(len(self._page_rules(rules)), 1)
        # Rules for other types, and exceptions, are as they were.
        self.assertEqual(adblock._network_rule("/t.js$script")[0]["trigger"]["resource-type"], ["script"])
        self.assertNotIn("resource-type", adblock._network_rule("@@||good.example/ad-")[0]["trigger"])
        self.assertEqual(adblock._network_rule("/^ad[0-9]/"), [])

    def test_badfilter_cancels_in_every_list(self):
        cancelled = adblock.cancelled_rules("[Adblock]\n||bad.example^$badfilter\n||x.example^$Image,script,badfilter\n")
        p = adblock.parse("[Adblock]\n||bad.example^\n||x.example^$script,image\n||kept.example^$script\n", cancelled)
        self.assertEqual(p.domains, set())
        urls = [r["trigger"]["url-filter"] for r in p.blocks]
        self.assertEqual(len(urls), 1)
        self.assertIn("kept", urls[0])

    def test_ghide_turns_off_generic_hiding_only(self):
        p = adblock.parse("[Adblock]\n##.ad\n/ad-$image\n@@||news.example^$ghide\n@@*$ghide,domain=a.example|b.example\n"
                          "@@*$ghide,xhr,domain=c.example\n")
        self.assertEqual(p.ghide, {"*news.example", "*a.example", "*b.example"})
        rules = adblock.list_rules(p, p.ghide)
        kinds = [r["action"]["type"] for r in rules]
        # generic hiding, then the exception (which so undoes nothing else), then the blocking rules
        self.assertEqual(kinds[:2], ["css-display-none", "ignore-previous-rules"])
        self.assertEqual(rules[1]["trigger"]["if-domain"], ["*a.example", "*b.example", "*news.example"])
        self.assertTrue(all(k == "block" for k in kinds[2:]))

    def test_new_rules_compile_in_webkit(self):
        from gi.repository import GLib, WebKit
        rules, _ = adblock.convert("[Adblock]\n##.ad\n/ad-\n/frame-$subdocument\n/x-$doc\n@@||n.example^$ghide\n")
        store = WebKit.UserContentFilterStore.new(tempfile.mkdtemp())
        loop, out = GLib.MainLoop(), []

        def saved(s, r):
            try:
                out.append(s.save_finish(r))
            except GLib.Error as e:
                out.append(e.message)
            loop.quit()
        store.save("t", GLib.Bytes.new(json.dumps(rules).encode()), None, saved)
        GLib.timeout_add_seconds(20, loop.quit)
        loop.run()
        self.assertIsInstance(out[0], WebKit.UserContentFilter, out)


class PageScriptTest(unittest.TestCase):
    """sofa/page.js: the keys its frames talk with (see "talking to other frames" there)."""

    PAGE_JS = os.path.join(os.path.dirname(__file__), "..", "sofa", "page.js")

    def source(self):
        with open(self.PAGE_JS, encoding="utf-8") as f:
            return f.read()

    def test_secret_never_sent(self):
        js = self.source()
        # The secret is read in one place only: to make a frame's key. Every message carries a key, or nothing.
        self.assertEqual(len(re.findall(r"\bTOKEN\b", js)), 2)  # declared, and used once
        self.assertIn('hmac(TOKEN, "sofa-frame:" + origin)', js)
        self.assertNotIn("token: TOKEN", js)
        for m in re.finditer(r"postMessage\((.*)", js):
            self.assertNotIn("TOKEN", m.group(1))

    def test_hmac_matches_python(self):
        # Run in JavaScriptCore in a helper process: it can't share a process with WebKit (see sofa/qr.py).
        js = self.source()
        start, end = js.index("  const K = ["), js.index("  const keys = new Map();")
        cases = [("key", ""), ("0123456789abcdef0123456789abcdef", "sofa-frame:https://example.com"),
                 ("k" * 64, "x" * 55), ("k" * 65, "x" * 56), ("k", "sofa-frame:https://例え.jp" + "y" * 200)]
        program = (
            # JavaScriptCore on its own has no TextEncoder: UTF-8 the old way.
            "var TextEncoder = function () {}; TextEncoder.prototype.encode = function (s) {"
            " var b = unescape(encodeURIComponent(s)), a = new Uint8Array(b.length);"
            " for (var i = 0; i < b.length; i++) a[i] = b.charCodeAt(i); return a; };"
            + js[start:end] + "; JSON.stringify(" + json.dumps(cases) + ".map(function (c) { return hmac(c[0], c[1]); }))")
        helper = ("import sys, gi; gi.require_version('JavaScriptCore', '6.0'); "
                  "from gi.repository import JavaScriptCore as J; c = J.Context(); "
                  "v = c.evaluate(sys.stdin.read(), -1); e = c.get_exception(); "
                  "print(e.get_message() if e else v.to_string())")
        import subprocess
        try:
            out = subprocess.run([sys.executable, "-I", "-c", helper], input=program, capture_output=True, text=True,
                                 timeout=30)
        except OSError as e:
            self.skipTest(str(e))
        if "require_version" in out.stderr or "Namespace" in out.stderr:
            self.skipTest("JavaScriptCore 6.0 is not installed")
        import hashlib
        import hmac
        expected = [hmac.new(k.encode(), m.encode(), hashlib.sha256).hexdigest() for k, m in cases]
        self.assertEqual(json.loads(out.stdout), expected, out.stderr)

class PhoneTest(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(phone.parse_command("m 3 -4"), ("m", 3.0, -4.0))
        self.assertEqual(phone.parse_command("z 1"), ("z", 1.0, 0.0))
        self.assertEqual(phone.parse_command("c"), ("c", 0.0, 0.0))
        self.assertEqual(phone.parse_command("m 99999 0"), ("m", 2000.0, 0.0))
        for bad in ("", "q", "m 1", "c 1", "m 1 x", "m nan 1", "m inf 1", "mm 1 2", "x" * 65):
            self.assertIsNone(phone.parse_command(bad), bad)

    def test_frames(self):
        def masked(op, payload, fin=True):
            m = b"\x01\x02\x03\x04"
            return bytes([(0x80 if fin else 0) | op, 0x80 | len(payload)]) + m + \
                bytes(c ^ m[i & 3] for i, c in enumerate(payload))
        f = masked(1, b"m 1 2")
        self.assertEqual(phone.read_frame(f), (1, b"m 1 2", len(f)))
        self.assertIsNone(phone.read_frame(f[:5]))
        with self.assertRaises(phone.ProtocolError):
            phone.read_frame(bytes([0x81, 5]) + b"m 1 2")  # not masked
        with self.assertRaises(phone.ProtocolError):
            phone.read_frame(masked(2, b"bin"))
        with self.assertRaises(phone.ProtocolError):
            phone.read_frame(masked(1, b"x", fin=False))
        with self.assertRaises(phone.ProtocolError):
            phone.read_frame(bytes([0x81, 0xFE, 0x01, 0x01]) + b"\0" * 4 + b"x" * 257)  # 257 bytes: too big
        self.assertEqual(phone.frame(1, b"p"), b"\x81\x01p")
        self.assertEqual(phone.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
        self.assertIsNone(phone.accept_key("short"))

    def test_lan_peers(self):
        ifaces = [("lo", "127.0.0.1", 8, True), ("wlan0", "192.168.1.27", 24, False),
                  ("wlan0", "fe80::1", 64, False), ("docker0", "172.17.0.1", 16, False)]
        self.assertTrue(phone.is_lan_peer("192.168.1.50", ifaces))
        self.assertTrue(phone.is_lan_peer("::ffff:192.168.1.50", ifaces))
        self.assertTrue(phone.is_lan_peer("fe80::abcd%wlan0", ifaces))
        self.assertFalse(phone.is_lan_peer("192.168.1.27", ifaces))   # this computer
        self.assertFalse(phone.is_lan_peer("127.0.0.1", ifaces))
        self.assertFalse(phone.is_lan_peer("192.168.2.50", ifaces))   # another subnet
        self.assertFalse(phone.is_lan_peer("8.8.8.8", ifaces))
        self.assertFalse(phone.is_lan_peer("not an address", ifaces))

    def test_bodies_and_limits(self):
        self.assertEqual(phone.extract('{"url": "a.com"}', "url"), "a.com")
        self.assertEqual(phone.extract("url=a.com%2Fb", "url"), "a.com/b")
        self.assertEqual(phone.extract("plain text", "text"), "plain text")
        self.assertEqual(phone.clean_text("a\nb\x00c\x1b"), "a\nbc")
        t = [0.0]
        rl = phone.RateLimit(3, clock=lambda: t[0])
        self.assertEqual([rl.allow() for _ in range(4)], [True, True, True, False])
        t[0] = 1.0
        self.assertTrue(rl.allow())

    def test_tokens(self):
        with tempfile.TemporaryDirectory() as d:
            ts = phone.TokenStore(os.path.join(d, "tok"))
            self.assertEqual(os.stat(os.path.join(d, "tok")).st_mode & 0o777, 0o600)
            self.assertTrue(ts.matches(ts.token))
            self.assertFalse(ts.matches("x" * 32))
            self.assertFalse(ts.matches(None))
            old = ts.token
            self.assertEqual(phone.TokenStore(os.path.join(d, "tok")).token, old)
            ts.rotate()
            self.assertFalse(ts.matches(old))

    def test_lockout(self):
        ts = phone.TokenStore.__new__(phone.TokenStore)
        ts.token = "a" * 32
        srv = phone.PhoneServer(ts, None, None)
        codes = [srv.admit("192.168.1.9", "wrong")[0] for _ in range(6)]
        self.assertEqual(codes, [403] * 5 + [429])
        self.assertEqual(srv.admit("192.168.1.9", "a" * 32)[0], 429)   # locked out, even with the token
        self.assertIsNone(srv.admit("192.168.1.10", "a" * 32))



class PhoneHttpsTest(unittest.TestCase):
    """The phone server over HTTPS and plain HTTP, through real sockets on this computer."""

    def setUp(self):
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("python3-cryptography is not installed")
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.calls = []

    def server(self, plain=False):
        import socket as so
        with so.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.tokens = phone.TokenStore(os.path.join(self.dir.name, "tok"))
        self.certs = phone.CertStore(self.dir.name, addresses=lambda: ["192.168.1.20", "127.0.0.1"])
        srv = phone.PhoneServer(self.tokens, self, lambda fn, *a: fn(*a), allow_peer=lambda _a: True, ports=[port],
                                tls=lambda: None if plain else self.certs.context())
        srv._advertise = lambda: None  # no announcing tests on the network
        self.assertEqual(srv.start(), port)
        self.addCleanup(lambda: srv.stop(wait=True))
        return srv

    # The actions the server calls (PhoneServer's [actions]).
    def __getattr__(self, name):
        if name in ("open_url", "type_text", "key", "pointer", "pointer_connected", "pointer_disconnected"):
            return lambda *a: self.calls.append((name, *a))
        raise AttributeError(name)

    def request(self, srv, method, path, body=b"", headers=None, tls=True):
        import http.client
        import ssl
        if tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
            c = http.client.HTTPSConnection("127.0.0.1", srv.port, context=ctx, timeout=5)
        else:
            c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
        c.request(method, path, body, headers or {})
        r = c.getresponse()
        out = r.status, dict(r.getheaders()), r.read()
        c.close()
        return out

    def test_certificate(self):
        from cryptography import x509
        certs = phone.CertStore(self.dir.name, addresses=lambda: ["192.168.1.20", "8.8.8.8", "fe80::1%eth0"])
        _ctx, created = certs.context()
        self.assertTrue(created)
        self.assertEqual(os.stat(certs.key_path).st_mode & 0o777, 0o600)
        with open(certs.cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        ips = {str(i) for i in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
               .get_values_for_type(x509.IPAddress)}
        self.assertEqual(ips, {"192.168.1.20", "fe80::1"})     # private addresses only
        self.assertLessEqual((cert.not_valid_after - cert.not_valid_before).days, 825)
        first = cert.serial_number
        self.assertFalse(certs.context()[1])                  # kept, not made again
        with open(certs.cert_path, "rb") as f:
            self.assertEqual(x509.load_pem_x509_certificate(f.read()).serial_number, first)

    def test_renewed_before_it_ends(self):
        certs = phone.CertStore(self.dir.name, addresses=lambda: [])
        certs.context()
        old = phone.CERT_DAYS
        phone.CERT_DAYS = 10
        try:
            os.unlink(certs.cert_path)
            certs.context()                                    # a certificate that ends in 10 days
        finally:
            phone.CERT_DAYS = old
        self.assertTrue(certs._ends_soon())
        certs.context()
        self.assertFalse(certs._ends_soon())

    def test_https(self):
        srv = self.server()
        self.assertTrue(srv.url() is None or srv.url().startswith("https://"))
        t = self.tokens.token
        status, headers, body = self.request(srv, "GET", f"/?t={t}", headers={"Host": f"192.168.1.20:{srv.port}"})
        self.assertEqual(status, 200)
        self.assertIn(b"trackpad", body)
        self.assertIn(f"connect-src 'self' wss://192.168.1.20:{srv.port}", headers["Content-Security-Policy"])
        self.assertEqual(self.request(srv, "POST", f"/type?t={t}", "hello".encode())[0], 200)
        self.assertEqual(self.request(srv, "POST", "/type?t=" + "0" * 32, b"no")[0], 403)
        self.assertEqual(self.calls, [("type_text", "hello")])

    def test_old_http_link_is_told_to_scan_again(self):
        srv = self.server()
        status, _h, body = self.request(srv, "GET", f"/?t={self.tokens.token}", tls=False)
        self.assertEqual(status, 400)
        self.assertIn(b"secure connection", body)
        self.assertNotIn(b"trackpad", body)

    def test_token_rotated_when_the_certificate_is_new(self):
        before = phone.TokenStore(os.path.join(self.dir.name, "tok")).token
        self.server()
        self.assertNotEqual(self.tokens.token, before)

    def test_trackpad_over_wss(self):
        import socket as so
        import ssl
        srv = self.server()
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        host = f"127.0.0.1:{srv.port}"
        with ctx.wrap_socket(so.create_connection(("127.0.0.1", srv.port), timeout=5)) as s:
            for origin, ok in ((f"http://{host}", False), (f"https://{host}", True)):
                if not ok:
                    with ctx.wrap_socket(so.create_connection(("127.0.0.1", srv.port), timeout=5)) as bad:
                        bad.sendall((f"GET /ws?t={self.tokens.token} HTTP/1.1\r\nHost: {host}\r\nOrigin: {origin}\r\n"
                                     "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                                     "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n").encode())
                        self.assertIn(b" 400 ", bad.recv(200))
                    continue
                s.sendall((f"GET /ws?t={self.tokens.token} HTTP/1.1\r\nHost: {host}\r\nOrigin: {origin}\r\n"
                           "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                           "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n").encode())
                self.assertIn(b" 101 ", s.recv(200))
                mask = b"\x01\x02\x03\x04"
                s.sendall(bytes([0x81, 0x80 | 1]) + mask + bytes([ord("p") ^ mask[0]]))
                self.assertEqual(s.recv(10), b"\x81\x01p")       # the keep-alive's answer, encrypted both ways
        self.assertIn(("pointer_connected",), self.calls)

    def test_plain_http_when_chosen(self):
        srv = self.server(plain=True)
        self.assertEqual(srv.scheme, "http")
        status, headers, _b = self.request(srv, "GET", f"/?t={self.tokens.token}", tls=False,
                                           headers={"Host": f"192.168.1.20:{srv.port}"})
        self.assertEqual(status, 200)
        self.assertIn("ws://192.168.1.20", headers["Content-Security-Policy"])
        self.assertFalse(os.path.exists(os.path.join(self.dir.name, "phone-cert.pem")))

class PlayerTest(unittest.TestCase):
    class Cookie:
        def __init__(self, name, value):
            self.name, self.value = name, value

        def get_name(self):
            return self.name

        def get_value(self):
            return self.value

    def test_cookie_header_is_a_private_file(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("XDG_CACHE_HOME")
            os.environ["XDG_CACHE_HOME"] = d
            try:
                path = player._header_conf([self.Cookie("sid", "a,b é"), self.Cookie("x", "y\r\nEvil: 1")])
            finally:
                if old is None:
                    del os.environ["XDG_CACHE_HOME"]
                else:
                    os.environ["XDG_CACHE_HOME"] = old
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            with open(path, encoding="utf-8") as f:
                line = f.read()
            header = "Cookie: sid=a,b é; x=yEvil: 1"
            self.assertEqual(line, f"http-header-fields-append=%{len(header.encode())}%{header}\n")


class GamePadTest(unittest.TestCase):
    def test_stick(self):
        self.assertEqual(gamepad.stick(0.1, 0.1), (0.0, 0.0))
        x, y = gamepad.stick(1.0, 0.0)
        self.assertAlmostEqual(x, 1.0)
        self.assertEqual(y, 0.0)
        small, _ = gamepad.stick(0.4, 0.0)
        self.assertLess(small, 0.4)  # curved: small pushes aim finely


class StoreTest(unittest.TestCase):
    def test_prefs_and_db(self):
        with tempfile.TemporaryDirectory() as d:
            p = Prefs(os.path.join(d, "s.json"))
            self.assertTrue(p.adblock)
            p.set_in("adblock_off_sites", "a.com", True)
            self.assertTrue(Prefs(os.path.join(d, "s.json")).in_set("adblock_off_sites", "a.com"))
            p.search_engine = "custom"
            p.custom_search = "https://s.example/?q=%s"
            self.assertEqual(p.engine.search_url("x y"), "https://s.example/?q=x%20y")
            db = Db(os.path.join(d, "t.db"))
            self.assertTrue(db.tiles())
            db.visit("https://a.com/1", "A")
            db.visit("https://a.com/2", "A2")
            db.visit("https://a.com/1", "")
            self.assertEqual(len(db.most_visited()), 1)  # one tile per site
            self.assertEqual(db.history()[0]["title"], "A")
            self.assertTrue(db.toggle_bookmark("https://a.com/1", "A"))
            self.assertFalse(db.toggle_bookmark("https://a.com/1", "A"))
            db.trim_history(0)
            self.assertEqual(db.history(), [])

    def test_prefs_migration(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            with open(path, "w") as f:
                json.dump({"stronger_adblock": True}, f)
            self.assertEqual(Prefs(path).lists_on, ["hosts", "popups", "easylist", "easyprivacy"])
            self.assertEqual(Prefs(os.path.join(d, "new.json")).lists_on, ["hosts", "popups"])


class XCursorTests(unittest.TestCase):
    @staticmethod
    def file(sizes):
        """An XCursor file with one solid image per size, its hotspot at (size/4, size/8)."""
        import struct
        toc, images, pos = [], [], 16 + 12 * len(sizes)
        for n in sizes:
            img = struct.pack("<IIIIIIIII", 36, 0xFFFD0002, n, 1, n, n, n // 4, n // 8, 0) + bytes([n]) * (n * n * 4)
            toc.append(struct.pack("<III", 0xFFFD0002, n, pos))
            images.append(img)
            pos += len(img)
        return struct.pack("<4sIII", b"Xcur", 16, 0x10000, len(sizes)) + b"".join(toc) + b"".join(images)

    def test_picks_the_nearest_size(self):
        data = self.file([24, 48, 96])
        w, h, xh, yh, px = cursors.read_xcursor(data, 48)
        self.assertEqual((w, h, xh, yh, len(px), px[0]), (48, 48, 12, 6, 48 * 48 * 4, 48))
        self.assertEqual(cursors.read_xcursor(data, 64)[0], 48)
        self.assertEqual(cursors.read_xcursor(data, 200)[0], 96)

    def test_rejects_junk(self):
        self.assertIsNone(cursors.read_xcursor(b"not a cursor", 48))
        self.assertIsNone(cursors.read_xcursor(self.file([48])[:200], 48))


if __name__ == "__main__":
    unittest.main()
