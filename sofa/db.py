# SPDX-License-Identifier: GPL-3.0-only
"""History, bookmarks, home tiles and downloads, in one SQLite file."""

import os
import sqlite3
import time
from urllib.parse import urlsplit

from . import paths

DEFAULT_TILES = [
    ("YouTube", "https://www.youtube.com/"),
    ("PeerTube", "https://joinpeertube.org/browse-content"),
    ("Twitch", "https://www.twitch.tv/"),
    ("Plex", "https://app.plex.tv/desktop/#!/"),
    ("Wikipedia", "https://www.wikipedia.org/"),
]


def host_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        h = urlsplit(url).hostname
    except ValueError:
        return None
    if not h:
        return None
    h = h.lower()
    return h[4:] if h.startswith("www.") else h


TWO_PART_TLDS = {"co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "com.au", "net.au", "org.au", "co.nz", "co.jp", "co.kr",
                 "com.br", "com.mx", "com.ar", "com.tr", "co.in", "co.za", "com.cn", "com.hk", "com.tw", "com.sg"}


def site_of(host: str | None) -> str | None:
    """The registrable domain, roughly: 'news.bbc.co.uk' -> 'bbc.co.uk'. Used to tell one site from another."""
    if not host:
        return None
    parts = host.lower().split(".")
    if len(parts) <= 2 or all(p.isdigit() for p in parts):
        return host.lower()
    return ".".join(parts[-3:] if ".".join(parts[-2:]) in TWO_PART_TLDS else parts[-2:])


def pretty_host(host: str) -> str:
    """'news.example.co.uk' -> 'News Example', for sites that never gave their page a title."""
    if all(c.isdigit() or c == "." for c in host) or ":" in host or "." not in host:
        return host
    parts = host.split(".")
    core = parts[:-2] if len(parts) > 2 and len(parts[-2]) <= 3 else parts[:-1]
    return " ".join(p[:1].upper() + p[1:] for p in (core or parts))


class Db:
    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(paths.data_dir(), "sofa.db")
        self.c = sqlite3.connect(self.path)
        self.c.row_factory = sqlite3.Row
        fresh = not self.c.execute("SELECT name FROM sqlite_master WHERE name='tiles'").fetchone()
        self.c.executescript("""
            CREATE TABLE IF NOT EXISTS history(url TEXT PRIMARY KEY, title TEXT, visits INTEGER, last INTEGER);
            CREATE TABLE IF NOT EXISTS bookmarks(url TEXT PRIMARY KEY, title TEXT, added INTEGER);
            CREATE TABLE IF NOT EXISTS tiles(url TEXT PRIMARY KEY, title TEXT, pos INTEGER);
            CREATE TABLE IF NOT EXISTS downloads(id INTEGER PRIMARY KEY, url TEXT, path TEXT, size INTEGER,
                                                 state TEXT, added INTEGER);
        """)
        if fresh:
            self.c.executemany("INSERT INTO tiles VALUES(?,?,?)",
                               [(u, t, i) for i, (t, u) in enumerate(DEFAULT_TILES)])
        self.c.commit()

    # History
    def visit(self, url: str, title: str | None):
        now = int(time.time())
        self.c.execute("""INSERT INTO history VALUES(?,?,1,?) ON CONFLICT(url) DO UPDATE
                          SET visits=visits+1, last=excluded.last, title=COALESCE(NULLIF(excluded.title,''), title)""",
                       (url, title or "", now))
        self.c.commit()

    def set_title(self, url: str, title: str):
        if title:
            self.c.execute("UPDATE history SET title=? WHERE url=?", (title, url))
            self.c.commit()

    def history(self, limit=500):
        return self.c.execute("SELECT * FROM history ORDER BY last DESC LIMIT ?", (limit,)).fetchall()

    def most_visited(self, limit=10):
        rows = self.c.execute("SELECT * FROM history ORDER BY visits DESC, last DESC LIMIT 200").fetchall()
        seen, out = set(), []
        for r in rows:  # one tile per site
            h = host_of(r["url"])
            if h and h not in seen:
                seen.add(h)
                out.append(r)
            if len(out) >= limit:
                break
        return out

    def remove_history(self, url):
        self.c.execute("DELETE FROM history WHERE url=?", (url,))
        self.c.commit()

    def clear_history(self):
        self.c.execute("DELETE FROM history")
        self.c.commit()

    def trim_history(self, days: int):
        if days == 0:
            self.clear_history()
        elif days > 0:
            self.c.execute("DELETE FROM history WHERE last < ?", (int(time.time()) - days * 86400,))
            self.c.commit()

    # Bookmarks
    def is_bookmarked(self, url):
        return self.c.execute("SELECT 1 FROM bookmarks WHERE url=?", (url,)).fetchone() is not None

    def toggle_bookmark(self, url, title) -> bool:
        if self.is_bookmarked(url):
            self.c.execute("DELETE FROM bookmarks WHERE url=?", (url,))
            on = False
        else:
            self.c.execute("INSERT INTO bookmarks VALUES(?,?,?)", (url, title or url, int(time.time())))
            on = True
        self.c.commit()
        return on

    def bookmarks(self, limit=1000):
        return self.c.execute("SELECT * FROM bookmarks ORDER BY added DESC LIMIT ?", (limit,)).fetchall()

    def remove_bookmark(self, url):
        self.c.execute("DELETE FROM bookmarks WHERE url=?", (url,))
        self.c.commit()

    # Home tiles
    def tiles(self):
        return self.c.execute("SELECT * FROM tiles ORDER BY pos").fetchall()

    def pin(self, url, title):
        pos = self.c.execute("SELECT COALESCE(MAX(pos),0)+1 FROM tiles").fetchone()[0]
        self.c.execute("INSERT OR REPLACE INTO tiles VALUES(?,?,?)", (url, title or url, pos))
        self.c.commit()

    def is_pinned(self, url):
        return self.c.execute("SELECT 1 FROM tiles WHERE url=?", (url,)).fetchone() is not None

    def unpin(self, url):
        self.c.execute("DELETE FROM tiles WHERE url=?", (url,))
        self.c.commit()

    # Downloads
    def add_download(self, url, path) -> int:
        cur = self.c.execute("INSERT INTO downloads(url,path,size,state,added) VALUES(?,?,0,'running',?)",
                             (url, path, int(time.time())))
        self.c.commit()
        return cur.lastrowid

    def update_download(self, id_, **kw):
        sets = ", ".join(f"{k}=?" for k in kw)
        self.c.execute(f"UPDATE downloads SET {sets} WHERE id=?", (*kw.values(), id_))
        self.c.commit()

    def downloads(self):
        return self.c.execute("SELECT * FROM downloads ORDER BY added DESC").fetchall()

    def remove_download(self, id_):
        self.c.execute("DELETE FROM downloads WHERE id=?", (id_,))
        self.c.commit()

    def clear_downloads(self):
        self.c.execute("DELETE FROM downloads WHERE state!='running'")
        self.c.commit()
