# Third-party notices

Sofa TV Browser for Linux is a port of Sofa TV Browser (https://github.com/sonalder-darlene/sofa-tv-browser) to
Linux desktops. Both are licensed under the GNU General Public License, version 3 only (see LICENSE).

The Linux port is new code (Python, GTK 4, libadwaita and WebKitGTK). It contains none of the Android app's
Kotlin code; it keeps its design, its texts where they still apply, its search engine list, its icon
(`packaging/app.sofabrowser.linux.svg`, from `art/icon.svg`, whose sofa glyph is from Material Symbols by Google,
Apache License 2.0, see LICENSE-APACHE-2.0.txt) and the two filter lists below. It does not bundle the Yuzu
Browser filter engine, AndroidX or Media3: ad blocking uses WebKit's content blockers, and video plays in mpv.

Tabs, the finer ad blocking (filter list choice, Clean up page, Remove element, pop-up and dialog handling, the
YouTube player-data script) and the phone remote are adapted from TVCouchBrowser for Linux and macOS (below).

It bundles the following third-party material.

## TVCouchBrowser for Linux and macOS

`sofa/phone.py`, `sofa/phone.html` and the clean-up and remove-element code in `sofa/page.js` are ported or adapted
from TVCouchBrowser for Linux and macOS (https://github.com/crash101g/tvcouchbrowser-desktop): its
`src/phone-server.js`, `src/pointer-protocol.js`, `src/tokens.js`, `src/ui/phone.html` and `src/preload.js`. The
filter list catalogue in `sofa/adblock.py` follows its `src/adblocker.js`.

> MIT License
>
> Copyright (c) 2026 crash101g
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
> documentation files (the "Software"), to deal in the Software without restriction, including without limitation
> the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
> to permit persons to whom the Software is furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or substantial portions
> of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
> THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
> CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
> IN THE SOFTWARE.

## qrcode-generator (the pairing QR code)

`sofa/vendor/qrcode.js` is QR Code Generator for JavaScript by Kazuhiko Arase (https://github.com/kazuhikoarase/
qrcode-generator), Copyright (c) 2009 Kazuhiko Arase, licensed under the MIT License (the licence text is the one
above, with that copyright line). "QR Code" is a registered trademark of DENSO WAVE INCORPORATED.

## Filter lists downloaded when turned on

The lists in Settings → Filter lists other than the two below are not bundled: Sofa downloads them from their
publishers when you turn them on, under their own licences: EasyList and EasyPrivacy (https://easylist.to,
GPL-3.0-or-later and CC BY-SA 3.0), the uBlock Origin lists (https://github.com/uBlockOrigin/uAssets, GPL-3.0),
AdGuard's lists (https://github.com/AdguardTeam/AdguardFilters, GPL-3.0), Peter Lowe's list
(https://pgl.yoyo.org/adservers/) and Fanboy's Annoyance list (https://easylist.to).

## StevenBlack/hosts (ad and tracker domains)

`sofa/data/blocklist-hosts.txt.gz` is derived from the unified hosts file at
https://github.com/StevenBlack/hosts.

> The MIT License (MIT)
>
> Copyright © 2026 Steven Black
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
> documentation files (the “Software”), to deal in the Software without restriction, including without
> limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
> the Software, and to permit persons to whom the Software is furnished to do so, subject to the following
> conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or substantial
> portions of the Software.
>
> THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
> LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO
> EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN
> AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
> OR OTHER DEALINGS IN THE SOFTWARE.

## HaGeZi's Pop-Up Ads list (pop-up and redirect domains)

`sofa/data/blocklist-popups.txt.gz` is the "Pop-Up Ads" list of HaGeZi's DNS blocklists
(https://github.com/hagezi/dns-blocklists), copyright HaGeZi, licensed under the GNU General Public License,
version 3, the same licence as Sofa TV Browser (see LICENSE).
The list's own disclaimer (no warranty; a listed domain is a filtering decision, not an accusation) is at
https://github.com/hagezi/dns-blocklists#disclaimer.
