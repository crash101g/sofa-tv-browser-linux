# SPDX-License-Identifier: GPL-3.0-only
"""Tests of Sofa's plain logic: run with  python3 -m unittest discover tests"""

import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json  # noqa: E402

from sofa import adblock, cursors, gamepad, phone  # noqa: E402
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


class AdBlockTest(unittest.TestCase):
    def test_hosts_file(self):
        d = adblock.read_domains("# c\n0.0.0.0 ads.example.com\n127.0.0.1 localhost\ntracker.net\n||pop.io^\n")
        self.assertEqual(d, {"ads.example.com", "tracker.net", "pop.io"})
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
