# Wish list

Ideas for later. Nothing here is being worked on yet.

## NordVPN control

Added 2026-11-07.

Control NordVPN's official Linux app (`nordvpn`) from Sofa:

- **Escape menu button:** shows Connected · Country or Off, and turns the VPN on or off.
- **Country picker:** a remote-friendly list of countries, plus Quickest server.
- **Settings:** auto-connect at startup and the kill switch.
- **Clear messages:** when the app isn't installed, the user isn't logged in, or the user isn't in the `nordvpn` group.

Setup needed first:

1. Install the NordVPN app.
2. Run `sudo usermod -aG nordvpn $USER`, then log out and back in.
3. Run `nordvpn login`.

The VPN covers the whole computer, not just Sofa, so videos played in mpv go through it too.

Estimated effort: small, about one session.

Rejected alternatives:

- **NordVPN SOCKS5 proxy for Sofa only:** few locations, not encrypted, and videos in mpv would need extra work.
- **A VPN built into Sofa:** needs root and NordVPN's undocumented API.
