# SPDX-License-Identifier: GPL-3.0-only
"""
QR codes for pairing a phone, made by the bundled qrcode-generator (MIT, Kazuhiko Arase) in JavaScriptCore.

JavaScriptCore can't be started in Sofa's own process next to WebKit, so this file also runs on its own, as a small
helper: `python3 qr.py TEXT` prints the code's rows of 0s and 1s, separated by commas.
"""

import json
import os
import sys

QR_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "qrcode.js")
MAKE = ("(function(){var q=qrcode(0,'M');q.addData(%s);q.make();var n=q.getModuleCount(),o=[];"
        "for(var r=0;r<n;r++){var s='';for(var c=0;c<n;c++)s+=q.isDark(r,c)?'1':'0';o.push(s)}return o.join(',')})()")


def matrix(text: str, done):
    """Calls [done](rows) with the code as a list of strings of '0' and '1', or done(None)."""
    from gi.repository import Gio, GLib

    try:
        proc = Gio.Subprocess.new([sys.executable, "-I", os.path.abspath(__file__), text],
                                  Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
    except GLib.Error:
        done(None)
        return

    def finished(p, res):
        try:
            _ok, out, _err = p.communicate_utf8_finish(res)
        except GLib.Error:
            out = ""
        rows = [r for r in (out or "").strip().split(",") if r]
        done(rows if rows and all(set(r) <= {"0", "1"} and len(r) == len(rows) for r in rows) else None)

    proc.communicate_utf8_async(None, None, finished)


def _main():
    import gi

    gi.require_version("JavaScriptCore", "6.0")
    from gi.repository import JavaScriptCore

    ctx = JavaScriptCore.Context()
    with open(QR_JS, encoding="utf-8") as f:
        ctx.evaluate(f.read(), -1)
    v = ctx.evaluate(MAKE % json.dumps(sys.argv[1]), -1)
    print(v.to_string())


if __name__ == "__main__":
    _main()
