#!/bin/sh
# Removes Sofa TV Browser. Your settings, bookmarks and history stay unless you pass --purge.
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
rm -rf "$PREFIX/sofa-tv-browser/app"
rm -f "$HOME/.local/bin/sofa-tv-browser" "$PREFIX/applications/app.sofabrowser.linux.desktop" \
      "$PREFIX/icons/hicolor/scalable/apps/app.sofabrowser.linux.svg"
update-desktop-database "$PREFIX/applications" 2>/dev/null || true
if [ "$1" = "--purge" ]; then
    rm -rf "$PREFIX/sofa-tv-browser" "${XDG_CONFIG_HOME:-$HOME/.config}/sofa-tv-browser" \
           "${XDG_CACHE_HOME:-$HOME/.cache}/sofa-tv-browser"
    echo "Sofa and all its data removed."
else
    echo "Sofa removed. Its data is in $PREFIX/sofa-tv-browser and ~/.config/sofa-tv-browser (--purge removes it)."
fi
