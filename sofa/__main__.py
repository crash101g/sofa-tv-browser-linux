# SPDX-License-Identifier: GPL-3.0-only
import sys


def _missing(e):
    sys.stderr.write(
        f"Sofa TV Browser can't start: {e}\n\n"
        "It needs GTK 4, libadwaita and WebKitGTK 6. On Pop!_OS / Ubuntu 24.04:\n"
        "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-webkit-6.0\n"
        "and for videos, game pads and the phone remote:\n"
        "  sudo apt install mpv gir1.2-manette-0.2 python3-cryptography\n"
        "(or run ./install.sh, which does all of this).\n")
    return 1


try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("WebKit", "6.0")
except (ImportError, ValueError) as e:
    sys.exit(_missing(e))

from .app import main  # noqa: E402

sys.exit(main(sys.argv))
