# SPDX-License-Identifier: GPL-3.0-only
"""
Sofa Player on Linux: mpv, fullscreen, with yt-dlp behind it.

A video file or stream the page plays is opened directly, with the page's cookies, referrer and user agent. A video
whose stream the page builds itself (Media Source, as on YouTube) is handed to mpv as the page's address, and
mpv's yt-dlp hook finds the stream. When mpv can't play it, Sofa says so and the site's player carries on.
"""

import json
import os
import shlex
import shutil
import socket
import tempfile
import time

from gi.repository import Gio, GLib

from . import paths

INPUT_CONF = """\
# Sofa TV Browser: remote and game pad keys for mpv (added to mpv's own bindings)
ESC quit-watch-later
BS quit-watch-later
BACK quit-watch-later
MBTN_BACK quit-watch-later
HOMEPAGE quit-watch-later
ENTER cycle pause
KP_ENTER cycle pause
PLAYPAUSE cycle pause
PLAY cycle pause
PAUSE cycle pause
MENU script-binding stats/display-stats-toggle
UP add volume 5
DOWN add volume -5
LEFT seek -10
RIGHT seek 10
PGUP seek 600
PGDWN seek -600
REWIND seek -30
FORWARD seek 30
CHANNEL_UP seek 60
CHANNEL_DOWN seek -60
PREV playlist-prev
NEXT playlist-next
STOP quit-watch-later
c cycle sub
a cycle audio
i script-binding stats/display-stats-toggle
GAMEPAD_ACTION_DOWN cycle pause
GAMEPAD_ACTION_RIGHT quit-watch-later
GAMEPAD_ACTION_LEFT cycle pause
GAMEPAD_ACTION_UP cycle sub
GAMEPAD_BACK script-binding stats/display-stats-toggle
GAMEPAD_START cycle pause
GAMEPAD_LEFT_SHOULDER seek -10
GAMEPAD_RIGHT_SHOULDER seek 10
GAMEPAD_LEFT_TRIGGER seek -60
GAMEPAD_RIGHT_TRIGGER seek 60
GAMEPAD_DPAD_LEFT seek -10
GAMEPAD_DPAD_RIGHT seek 10
GAMEPAD_DPAD_UP add volume 5
GAMEPAD_DPAD_DOWN add volume -5
"""

# How long mpv must run for its start to count: an error before this means the stream couldn't be played.
FAILED_WITHIN_S = 8

# The phone remote's keys, as the mpv keys of INPUT_CONF.
PHONE_KEYS = {"up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT", "ok": "ENTER", "enter": "ENTER",
              "playpause": "PLAYPAUSE", "back": "ESC", "home": "ESC", "menu": "MENU", "watch": "ENTER",
              "backspace": "BS"}


def available(command: str) -> bool:
    argv = shlex.split(command or "mpv")
    return bool(argv) and shutil.which(argv[0]) is not None


def is_mpv(command: str) -> bool:
    argv = shlex.split(command or "mpv")
    return bool(argv) and os.path.basename(argv[0]) == "mpv"


def _cookie_file(cookies) -> str:
    """WebKit cookies as a Netscape cookies.txt for yt-dlp, readable only by the user."""
    fd, path = tempfile.mkstemp(prefix="sofa-cookies-", suffix=".txt", dir=paths.sub(paths.cache_dir(), "tmp"))
    with os.fdopen(fd, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for c in cookies:
            domain = c.get_domain() or ""
            exp = c.get_expires()
            expires = int(exp.to_unix()) if exp else 0
            f.write("\t".join([
                domain, "TRUE" if domain.startswith(".") else "FALSE", c.get_path() or "/",
                "TRUE" if c.get_secure() else "FALSE", str(expires), c.get_name(), c.get_value() or "",
            ]) + "\n")
    return path


class Player:
    """Runs one external player at a time and reports how it ended."""

    def __init__(self, prefs):
        self.prefs = prefs
        self.proc: Gio.Subprocess | None = None
        self._tmp: list[str] = []
        self.ipc = None
        conf_dir = paths.sub(paths.config_dir(), "mpv")
        self.input_conf = os.path.join(conf_dir, "input.conf")
        # Sofa's own file (the user's mpv input.conf is left alone): rewritten when Sofa's bindings change.
        try:
            with open(self.input_conf) as f:
                current = f.read()
        except OSError:
            current = None
        if current != INPUT_CONF:
            with open(self.input_conf, "w") as f:
                f.write(INPUT_CONF)

    @property
    def running(self):
        return self.proc is not None

    def stop(self):
        if self.proc:
            self.proc.send_signal(15)

    def send_key(self, name: str) -> bool:
        """A key from the phone remote, pressed in mpv through its IPC socket."""
        key = PHONE_KEYS.get(name)
        if not (self.proc and self.ipc and key):
            return False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                s.connect(self.ipc)
                s.sendall((json.dumps({"command": ["keypress", key]}) + "\n").encode())
                s.recv(512)  # mpv's answer; closing before it comes makes mpv log a write error
            return True
        except OSError:
            return False

    def play(self, *, url: str, page_url: str, title: str, user_agent: str | None, cookies, referrer: str | None,
             direct: bool, private: bool, max_height: int, start: float = 0.0, subtitles=(), on_started=None,
             on_ended=None):
        """
        Plays [url] (a stream when [direct], else a page for yt-dlp). [on_started] runs once the player has kept
        going for a few seconds; [on_ended](ok, message) when it exits.
        """
        if self.proc:
            self.stop()
        self.ipc = None
        cmd = self.prefs.player_command.strip() or "mpv"
        argv = shlex.split(cmd)
        if not is_mpv(cmd):
            # Another player gets the address only; "{url}" in the command places it.
            argv = [a.replace("{url}", url) for a in argv]
            if not any(url in a for a in argv):
                argv.append(url)
        else:
            argv += [
                "--fs", "--force-window=immediate", "--keep-open=no", "--idle=no", "--input-terminal=no",
                "--hwdec=auto-safe", "--input-gamepad=yes", f"--input-conf={self.input_conf}",
                f"--title={title or 'Sofa'} — Sofa", "--osd-on-seek=msg-bar",
                "--msg-level=all=error",
            ]
            if not private:
                argv += ["--save-position-on-quit",
                         f"--watch-later-dir={paths.sub(paths.data_dir(), 'watch-later')}"]
            else:
                argv += ["--no-resume-playback"]
            if start > 5:
                argv.append(f"--start={int(start)}")
            if user_agent:
                argv.append(f"--user-agent={user_agent}")
            if referrer:
                argv.append(f"--referrer={referrer}")
            if direct:
                argv.append("--ytdl=no")
                if cookies:
                    header = "; ".join(f"{c.get_name()}={c.get_value()}" for c in cookies)
                    argv.append(f"--http-header-fields=Cookie: {header.replace(',', '%2C')}")
                for t in subtitles:
                    if t.get("src", "").startswith(("http://", "https://")):
                        argv.append(f"--sub-file={t['src']}")
            else:
                argv.append("--ytdl=yes")
                h = max_height or 2160
                argv.append(f"--ytdl-format=bestvideo[height<=?{h}]+bestaudio/best[height<=?{h}]/best")
                if cookies:
                    path = _cookie_file(cookies)
                    self._tmp.append(path)
                    argv.append(f"--ytdl-raw-options-append=cookies={path}")
                yt = shutil.which("yt-dlp")
                if yt:
                    argv.append(f"--script-opts-append=ytdl_hook-ytdl_path={yt}")
            run = GLib.get_user_runtime_dir() or paths.sub(paths.cache_dir(), "tmp")
            self.ipc = os.path.join(paths.sub(run, "sofa-tv-browser"), f"mpv-{os.getpid()}.sock")
            os.chmod(os.path.dirname(self.ipc), 0o700)
            argv.append(f"--input-ipc-server={self.ipc}")
            argv += shlex.split(self.prefs.player_extra_args or "")
            argv += ["--", url]
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as e:
            if on_ended:
                on_ended(False, e.message)
            return
        self.proc = proc
        started_at = time.monotonic()
        started = [False]

        def tick():
            if self.proc is proc and not started[0]:
                started[0] = True
                if on_started:
                    on_started()
            return False

        GLib.timeout_add(2500, tick)

        def finished(p, res):
            err = ""
            try:
                _ok, _out, err = p.communicate_utf8_finish(res)
            except GLib.Error:
                pass
            if self.proc is p:
                self.proc = None
            for path in self._tmp:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            self._tmp.clear()
            code = p.get_exit_status() if p.get_if_exited() else -1
            quick = time.monotonic() - started_at < FAILED_WITHIN_S
            ok = code in (0, 4) or (not quick and code != 2) or p.get_if_signaled()
            line = next((ln for ln in reversed((err or "").splitlines()) if ln.strip()), "")
            if on_ended:
                on_ended(ok, line)

        proc.communicate_utf8_async(None, None, finished)
