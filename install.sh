#!/bin/sh
# Installs Sofa TV Browser for the current user (no root needed for the app itself).
# The system packages it needs are installed with apt, which asks for your password.
set -e
HERE="$(dirname "$(readlink -f "$0")")"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN="$HOME/.local/bin"
DEST="$PREFIX/sofa-tv-browser/app"

PKGS="python3-gi python3-cryptography gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-webkit-6.0 gir1.2-manette-0.2 mpv"
MISSING=""
for p in $PKGS; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || MISSING="$MISSING $p"
done
if [ -n "$MISSING" ]; then
    echo "Installing the system packages Sofa needs:$MISSING"
    sudo apt-get install -y $MISSING
fi
# Pop!_OS / Ubuntu 22.04 has every package above, but its libadwaita (1.1) is too old for Sofa (1.5 or newer).
if ! python3 -c 'import gi; gi.require_version("Adw", "1"); from gi.repository import Adw
raise SystemExit((Adw.get_major_version(), Adw.get_minor_version()) < (1, 5))' 2>/dev/null; then
    echo "Sofa needs libadwaita 1.5 or newer, and this system's is older (Pop!_OS / Ubuntu 22.04 has 1.1)."
    echo "Upgrading to 24.04 is the way: see \"Pop!_OS 22.04\" in README.md. Nothing was installed."
    exit 1
fi

echo "Installing Sofa to $DEST"
rm -rf "$DEST"
mkdir -p "$DEST" "$BIN" "$PREFIX/applications" "$PREFIX/icons/hicolor/scalable/apps"
cp -r "$HERE/sofa" "$DEST/"
find "$DEST" -name __pycache__ -prune -exec rm -rf {} +
cp "$HERE/sofa-tv-browser" "$HERE/LICENSE" "$HERE/NOTICE.md" "$DEST/"
ln -sf "$DEST/sofa-tv-browser" "$BIN/sofa-tv-browser"
cp "$HERE/packaging/app.sofabrowser.linux.desktop" "$PREFIX/applications/"
cp "$HERE/packaging/app.sofabrowser.linux.svg" "$PREFIX/icons/hicolor/scalable/apps/"
update-desktop-database "$PREFIX/applications" 2>/dev/null || true
gtk-update-icon-cache -q "$PREFIX/icons/hicolor" 2>/dev/null || true

if ! command -v yt-dlp >/dev/null 2>&1; then
    echo
    echo "Note: yt-dlp was not found. mpv uses it to play videos from sites like YouTube."
    echo "Ubuntu's own package is often too old for YouTube; the current release is best:"
    echo "  sudo apt install pipx && pipx install yt-dlp      (then: pipx upgrade yt-dlp now and then)"
fi
case ":$PATH:" in *":$BIN:"*) ;; *) echo; echo "Note: $BIN is not in your PATH; start Sofa from the app grid." ;; esac
echo
echo "Done. Start \"Sofa TV Browser\" from the app grid, or run: sofa-tv-browser"
