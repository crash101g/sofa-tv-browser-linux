# SPDX-License-Identifier: GPL-3.0-only
"""
Your phone as a trackpad, keyboard and remote: a small web server on the home network that serves the phone page
(sofa/phone.html) and takes its commands.

Ported from TVCouchBrowser for Linux (src/phone-server.js, src/pointer-protocol.js, src/tokens.js), Copyright (c)
2026 crash101g, MIT License; see NOTICE.md.

It runs only while it is turned on (Pair phone, or Settings) and Sofa's window is on screen. Every request needs the
pairing token, a 128-bit random value compared in constant time, and must come from a private address on this
computer's own subnet; programs on this computer itself are refused. Five wrong tokens from one address block it for
ten minutes. Requests carry only an address, text, a key name or a short pointer command, never a script. The
trackpad's WebSocket must come from the phone page itself (its Origin is checked), at most two at a time, and
messages past 120 a second are dropped.

It is HTTPS, with a certificate Sofa makes for itself and keeps (CertStore), so someone watching the home network's
traffic can't read the token or what is typed. No authority vouches for that certificate, so a phone warns about it
the first time; after that it warns again only if the certificate changes. Plain HTTP is there for phones that won't
take the certificate, when the user turns it on.
"""

import base64
import datetime
import hashlib
import hmac
import http.server
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import socketserver
import ssl
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit

from . import paths

PORTS = [8765, 8766, 8767, 8768]
MAX_SOCKETS = 2
MAX_POINTER_RATE = 120          # messages a second per trackpad; more are dropped
MAX_BODY = 16384
MAX_TEXT = 2000
MAX_FAILURES = 5
FAIL_WINDOW_S = 600
BLOCK_S = 600
KEYS = {"up", "down", "left", "right", "ok", "back", "menu", "playpause", "enter", "backspace", "watch", "home"}
CERT_DAYS = 825                 # the longest Apple accepts for a TLS server certificate
RENEW_DAYS = 30                 # a new certificate this long before the old one ends
HOST_HEADER = re.compile(r"^([A-Za-z0-9.-]+(:\d{1,5})?|\[[0-9A-Fa-f:.%]+\](:\d{1,5})?)$")
EXPIRED_PAGE = ("<!doctype html><meta name=viewport content='width=device-width'>"
                "<body style='font:18px system-ui,sans-serif;padding:24px'>This link has expired. On the TV, open "
                "the menu, choose <b>Phone</b> and scan the new QR code.</body>")
UPGRADE_PAGE = ("HTTP/1.1 400 Bad Request\r\nContent-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\n"
                "Connection: close\r\n\r\n" + EXPIRED_PAGE.replace("This link has expired", "Sofa's phone remote "
                                                                       "now uses a secure connection")).encode("utf-8")
PHONE_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phone.html")

# ---- the pairing token -------------------------------------------------------------------------------------------


class TokenStore:
    """The token, in a file only this user can read (~/.config/sofa-tv-browser/phone-token)."""

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(paths.config_dir(), "phone-token")
        self.token = self._load() or self.rotate()

    def _load(self):
        try:
            with open(self.path, encoding="ascii") as f:
                t = f.read().strip()
            return t if re.fullmatch(r"[0-9a-f]{32}", t) else None
        except OSError:
            return None

    def rotate(self) -> str:
        """A new token: every phone paired before has to scan the QR code again."""
        t = secrets.token_hex(16)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(t)
        os.replace(tmp, self.path)
        self.token = t
        return t

    def matches(self, candidate) -> bool:
        if not isinstance(candidate, str):
            return False
        h = lambda s: hashlib.sha256(s.encode("utf-8", "replace")).digest()  # noqa: E731
        return hmac.compare_digest(h(candidate), h(self.token))


class CertUnavailable(Exception):
    """The certificate can't be made (python3-cryptography is missing) or read."""


class CertStore:
    """
    The phone page's certificate and key, made once and kept in files only this user can read
    (~/.config/sofa-tv-browser/phone-cert.pem and phone-key.pem). A phone that accepted it isn't asked again until
    it is renewed, a month before it ends.
    """

    def __init__(self, directory: str | None = None, addresses=None):
        d = directory or paths.config_dir()
        self.cert_path = os.path.join(d, "phone-cert.pem")
        self.key_path = os.path.join(d, "phone-key.pem")
        self._addresses = addresses or (lambda: [a for _n, a, _p, loop in interfaces() if not loop])

    def context(self):
        """(an SSLContext for the server, whether the certificate is brand new). Raises CertUnavailable."""
        created = not os.path.exists(self.cert_path)
        if created or self._ends_soon():
            self._make()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            ctx.load_cert_chain(self.cert_path, self.key_path)
        except (OSError, ssl.SSLError) as e:
            raise CertUnavailable(f"the phone remote's certificate can't be read: {e}") from e
        return ctx, created

    def _ends_soon(self) -> bool:
        try:
            from cryptography import x509
        except ImportError:
            return False  # can't renew without it: keep using the one there is
        try:
            with open(self.cert_path, "rb") as f:
                cert = x509.load_pem_x509_certificate(f.read())
        except (OSError, ValueError):
            return True
        ends = getattr(cert, "not_valid_after_utc", None) or \
            cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
        return ends - datetime.datetime.now(datetime.timezone.utc) < datetime.timedelta(days=RENEW_DAYS)

    def _make(self):
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
        except ImportError as e:
            raise CertUnavailable("a secure connection needs python3-cryptography: sudo apt install "
                                  "python3-cryptography") from e
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Sofa TV Browser")])
        sans, seen = [x509.DNSName("localhost")], set()
        for a in self._addresses():
            try:
                ip = ipaddress.ip_address(_plain(a))
            except ValueError:
                continue
            if ip not in seen and is_private(ip):
                seen.add(ip)
                sans.append(x509.IPAddress(ip))
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder()
                .subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=5))
                .not_valid_after(now + datetime.timedelta(days=CERT_DAYS))
                .add_extension(x509.SubjectAlternativeName(sans), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .sign(key, hashes.SHA256()))
        key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        try:
            _write_private(self.key_path, key_pem)
            _write_private(self.cert_path, cert.public_bytes(serialization.Encoding.PEM))
        except OSError as e:
            raise CertUnavailable(f"the phone remote's certificate can't be saved: {e}") from e


def _write_private(path, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


# ---- who may connect ---------------------------------------------------------------------------------------------

def _plain(addr: str) -> str:
    """Sockets report IPv4 peers on a dual-stack listener as ::ffff:a.b.c.d."""
    a = (addr or "").split("%", 1)[0]
    return a[7:] if a.lower().startswith("::ffff:") and "." in a else a


def is_private(ip) -> bool:
    if ip.version == 4:
        return any(ip in ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                                           "169.254.0.0/16"))
    return ip in ipaddress.ip_network("fe80::/10") or ip in ipaddress.ip_network("fc00::/7")


_ifaces_cache = (0.0, [])


def interfaces():
    """This computer's addresses: [(name, address, prefix length, is loopback)], from `ip -j addr`."""
    global _ifaces_cache
    at, cached = _ifaces_cache
    if time.monotonic() - at < 10:
        return cached
    out = []
    try:
        data = json.loads(subprocess.run(["ip", "-j", "addr", "show"], capture_output=True, text=True,
                                         timeout=3).stdout or "[]")
        for link in data:
            loop = "LOOPBACK" in (link.get("flags") or [])
            for a in link.get("addr_info") or []:
                if a.get("local") and a.get("prefixlen") is not None:
                    out.append((link.get("ifname", ""), a["local"], int(a["prefixlen"]), loop))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    _ifaces_cache = (time.monotonic(), out)
    return out


def is_lan_peer(raw_addr: str, ifaces=None) -> bool:
    """
    True for private or link-local peers on the same subnet as one of this computer's addresses. This computer
    itself is refused, so other programs on it can't drive the browser.
    """
    try:
        peer = ipaddress.ip_address(_plain(raw_addr))
    except ValueError:
        return False
    if not is_private(peer):
        return False
    on_subnet = False
    for _name, local, prefix, loop in (interfaces() if ifaces is None else ifaces):
        try:
            mine = ipaddress.ip_address(_plain(local))
        except ValueError:
            continue
        if mine == peer:
            return False
        if loop or mine.version != peer.version:
            continue
        if peer in ipaddress.ip_network(f"{mine}/{prefix}", strict=False):
            on_subnet = True
    return on_subnet


VIRTUAL = re.compile(r"^(lo|docker|br-|veth|virbr|tun|tap|wg|zt|tailscale|vmnet|vboxnet|lxc|lxd)")


def lan_address():
    """The address a phone should use: the one on the default route, not a VM or container bridge."""
    v4 = {}
    for name, addr, _p, loop in interfaces():
        if not loop and ":" not in addr:
            v4.setdefault(name, addr)
    try:
        with open("/proc/net/route", encoding="ascii") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) > 1 and parts[1] == "00000000" and parts[0] in v4:
                    return v4[parts[0]]
    except OSError:
        pass
    for name, addr in v4.items():
        if not VIRTUAL.match(name) and is_private(ipaddress.ip_address(addr)):
            return addr
    return None


# ---- the trackpad's wire format, and the part of WebSocket (RFC 6455) it needs ------------------------------------
#
# Messages are short text frames: a command letter, optionally followed by numbers.
#   m dx dy  move the pointer        s dx dy  scroll under the pointer
#   c        click                   2        double-click
#   d / u    press / release (drag)  l        long press (a link's menu)
#   z n      zoom in (n > 0) or out  x        remove the element under the pointer
#   k        clean up the page       p        keep-alive; the TV answers "p"

OPS = "msc2dulzxkp"
MAX_DELTA = 2000
MAX_MESSAGE = 64
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_TEXT, OP_CLOSE, OP_PING, OP_PONG = 1, 8, 9, 10


def parse_command(msg: str):
    """(op, a, b) for a valid message; None if it is malformed, too long or has an unknown command."""
    if not msg or len(msg) > MAX_MESSAGE:
        return None
    parts = msg.strip().split()
    if not parts or len(parts[0]) != 1 or parts[0] not in OPS:
        return None
    op = parts[0]
    needs = 2 if op in "ms" else 1 if op == "z" else 0
    if len(parts) != needs + 1:
        return None
    nums = [0.0, 0.0]
    for i in range(needs):
        try:
            v = float(parts[i + 1])
        except ValueError:
            return None
        if v != v or v in (float("inf"), float("-inf")):
            return None
        nums[i] = max(-MAX_DELTA, min(MAX_DELTA, v))
    return op, nums[0], nums[1]


def accept_key(key):
    """The Sec-WebSocket-Accept value for a client key, or None if it isn't a valid 16-byte nonce."""
    if not isinstance(key, str):
        return None
    k = key.strip()
    if not re.fullmatch(r"[A-Za-z0-9+/]{22}==", k):
        return None
    return base64.b64encode(hashlib.sha1((k + GUID).encode("ascii")).digest()).decode("ascii")


class ProtocolError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def read_frame(buf: bytes, max_payload: int = 256):
    """
    One frame from the start of [buf]: (opcode, payload, bytes used), or None if more bytes are needed. Clients
    must mask; fragmented, binary and oversized frames raise a ProtocolError carrying the close code to send.
    """
    if len(buf) < 2:
        return None
    b0, b1 = buf[0], buf[1]
    fin = bool(b0 & 0x80)
    if b0 & 0x70:
        raise ProtocolError(1002, "reserved bits set")
    opcode = b0 & 0x0F
    if not b1 & 0x80:
        raise ProtocolError(1002, "client frame not masked")
    length = b1 & 0x7F
    at = 2
    if length == 126:
        if len(buf) < 4:
            return None
        length, at = int.from_bytes(buf[2:4], "big"), 4
    elif length == 127:
        if len(buf) < 10:
            return None
        length, at = int.from_bytes(buf[2:10], "big"), 10
    if opcode >= 8 and (length > 125 or not fin):
        raise ProtocolError(1002, "bad control frame")
    if opcode in (0, 2):
        raise ProtocolError(1003, "only unfragmented text frames are accepted")
    if opcode not in (OP_TEXT, OP_CLOSE, OP_PING, OP_PONG):
        raise ProtocolError(1002, f"unknown opcode {opcode}")
    if not fin:
        raise ProtocolError(1003, "fragmented messages are not accepted")
    if length > max_payload:
        raise ProtocolError(1009, "message too big")
    if len(buf) < at + 4 + length:
        return None
    mask = buf[at:at + 4]
    payload = bytes(c ^ mask[i & 3] for i, c in enumerate(buf[at + 4:at + 4 + length]))
    return opcode, payload, at + 4 + length


def frame(opcode: int, payload: bytes) -> bytes:
    """An unmasked server frame (servers never mask). Payloads here are always short."""
    head = bytes([0x80 | opcode, len(payload)]) if len(payload) < 126 else \
        bytes([0x80 | opcode, 126, len(payload) >> 8, len(payload) & 0xFF])
    return head + payload


class RateLimit:
    """A token bucket: [per_second] messages a second on average, bursts up to the same number."""

    def __init__(self, per_second, clock=time.monotonic):
        self.per_second, self.clock = per_second, clock
        self.tokens = float(per_second)
        self.last = clock()

    def allow(self) -> bool:
        now = self.clock()
        self.tokens = min(self.per_second, self.tokens + (now - self.last) * self.per_second)
        self.last = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


def extract(body: str, name: str) -> str:
    """A JSON object, a form body or plain text, so an iPhone Shortcut can send any of them."""
    t = body.strip()
    if t.startswith("{"):
        try:
            v = json.loads(t).get(name)
        except (ValueError, AttributeError):
            return ""
        return "" if v is None else str(v)
    if t.startswith(name + "="):
        return (parse_qs(t).get(name) or [""])[0]
    return body


def clean_text(text: str) -> str:
    """Newlines stay; other control characters don't."""
    return "".join(c for c in text if c == "\n" or c >= " ")[:MAX_TEXT]


# ---- the server --------------------------------------------------------------------------------------------------

class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 8

    def __init__(self, addr, family, phone, tls=None):
        self.address_family = family
        self.phone = phone
        self.tls = tls
        super().__init__(addr, _Handler, bind_and_activate=False)
        if family == socket.AF_INET6:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        try:
            self.server_bind()
            self.server_activate()
        except OSError:
            self.server_close()
            raise

    def verify_request(self, request, client_address):
        # Anything but a device on this home network is dropped without an answer.
        return self.phone.allow_peer(client_address[0])

    def finish_request(self, request, client_address):
        # On the request's own thread, so a slow or silent phone holds up no one else.
        if self.tls is None:
            super().finish_request(request, client_address)
            return
        request.settimeout(_Handler.timeout)
        try:
            first = request.recv(1, socket.MSG_PEEK)
            if first and first != b"\x16":  # not a TLS handshake: an old http:// link
                request.recv(8192)
                request.sendall(UPGRADE_PAGE)
                return
            if not first:
                return
            conn = self.tls.wrap_socket(request, server_side=True)
        except (OSError, ssl.SSLError):
            return  # a phone that turned the certificate down closes the connection here
        try:
            super().finish_request(conn, client_address)
        finally:
            try:
                conn.close()
            except OSError:
                pass


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "Sofa"
    sys_version = ""
    timeout = 15

    def log_message(self, *_a):
        pass

    @property
    def phone(self):
        return self.server.phone

    def _send(self, code, ctype, body, ws_host=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        # The phone page may open its trackpad socket back to this server, and nowhere else.
        ws = "wss" if self.phone.scheme == "https" else "ws"
        connect = f"; connect-src 'self' {ws}://{ws_host}" if ws_host and HOST_HEADER.match(ws_host) else ""
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         f"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'{connect}")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _ok(self, ok):
        self._send(200 if ok else 422, "application/json", '{"ok":true}' if ok else '{"ok":false}')

    def _url(self):
        u = urlsplit(self.path)
        return u.path, (parse_qs(u.query).get("t") or [None])[0], parse_qs(u.query)

    def _admitted(self, token) -> bool:
        refusal = self.phone.admit(self.client_address[0], token)
        if refusal is None:
            return True
        code, body, delay = refusal
        if delay:
            time.sleep(delay)
        self._send(code, "text/html; charset=utf-8" if code == 403 else "text/plain", body)
        return False

    def do_GET(self):
        path, token, _q = self._url()
        if path == "/favicon.ico":
            self._send(404, "text/plain", "")
            return
        if not self._admitted(token):
            return
        if path == "/":
            with open(PHONE_PAGE, encoding="utf-8") as f:
                self._send(200, "text/html; charset=utf-8", f.read(), self.headers.get("Host"))
        elif path == "/ws":
            self._websocket()
        else:
            self._send(404, "text/plain", "Not found")

    def do_POST(self):
        path, token, q = self._url()
        if not self._admitted(token):
            return
        if path not in ("/open", "/type", "/key"):
            self._send(404, "text/plain", "Not found")
            return
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = -1
        if size < 0 or size > MAX_BODY:
            self._send(400, "text/plain", "Bad request")
            return
        body = self.rfile.read(size).decode("utf-8", "replace") if size else ""
        if path == "/open":
            target = extract(body, "url").strip()
            if not target or len(target) > 4096:
                self._send(400, "text/plain", "No URL")
                return
            self.phone.dispatch("open_url", target)
            self._ok(True)
        elif path == "/type":
            self.phone.dispatch("type_text", clean_text(extract(body, "text")))
            self._ok(True)
        else:
            k = (q.get("k") or [""])[0]
            ok = k in KEYS
            if ok:
                self.phone.dispatch("key", k)
            self._ok(ok)

    def _refuse_ws(self, code, reason):
        self.send_response(code, reason)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "7")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b"Refused")
        self.close_connection = True

    def _websocket(self):
        """
        The trackpad: a WebSocket that stays open and carries small pointer commands. The Origin must be this
        server, so another site the phone has open can't drive the TV even if it learned the token.
        """
        host, origin = self.headers.get("Host"), self.headers.get("Origin")
        if (self.headers.get("Upgrade") or "").lower() != "websocket" or \
                self.headers.get("Sec-WebSocket-Version") != "13" or (origin and host and origin != f"{self.phone.scheme}://{host}"):
            self._refuse_ws(400, "Bad Request")
            return
        accept = accept_key(self.headers.get("Sec-WebSocket-Key"))
        if not accept:
            self._refuse_ws(400, "Bad Request")
            return
        if not self.phone.take_trackpad(self.connection):
            self._refuse_ws(429, "Too Many Requests")
            return
        sock = self.connection
        try:
            sock.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode("ascii"))
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(30)  # the phone sends a keep-alive every 10 s
            self.phone.dispatch("pointer_connected")
            self._ws_loop(sock)
        except OSError:
            pass
        finally:
            self.phone.drop_trackpad(sock)
            self.phone.dispatch("pointer_disconnected")
            self.close_connection = True

    def _ws_loop(self, sock):
        limit = RateLimit(MAX_POINTER_RATE)
        pending = b""
        while True:
            data = sock.recv(4096)
            if not data:
                return
            pending += data
            try:
                while True:
                    f = read_frame(pending)
                    if f is None:
                        break
                    opcode, payload, used = f
                    pending = pending[used:]
                    if opcode == OP_CLOSE:
                        sock.sendall(frame(OP_CLOSE, (1000).to_bytes(2, "big")))
                        return
                    if opcode == OP_PING:
                        sock.sendall(frame(OP_PONG, payload))
                    if opcode != OP_TEXT or not limit.allow():
                        continue  # a flood just drops messages
                    cmd = parse_command(payload.decode("utf-8", "replace"))
                    if cmd is None:
                        continue
                    if cmd[0] == "p":
                        sock.sendall(frame(OP_TEXT, b"p"))
                    else:
                        self.phone.dispatch("pointer", *cmd)
            except ProtocolError as e:
                sock.sendall(frame(OP_CLOSE, e.code.to_bytes(2, "big")))
                return


class PhoneServer:
    """
    [actions] has open_url(text), type_text(text), key(name), pointer(op, a, b), pointer_connected() and
    pointer_disconnected(); [dispatch] calls them on the main loop.
    """

    def __init__(self, tokens: TokenStore, actions, dispatch, allow_peer=is_lan_peer, ports=PORTS, tls=None):
        """
        [tls] returns (an SSLContext, whether its certificate is brand new) for HTTPS, or None for plain HTTP; it may
        raise CertUnavailable, and then the server doesn't start.
        """
        self.tokens = tokens
        self.tls = tls
        self.scheme = "http"
        self.actions = actions
        self._dispatch = dispatch
        self.allow_peer = allow_peer
        self.ports = ports
        self.server = None
        self.port = 0
        self._lock = threading.Lock()
        self._failures: dict[str, list] = {}
        self._trackpads = set()
        self._advertiser = None

    @property
    def running(self):
        return self.server is not None

    def start(self) -> int:
        """The port it listens on, or 0 if every port is taken. Raises CertUnavailable if HTTPS can't be set up."""
        if self.server:
            return self.port
        tls = self.tls() if self.tls else None
        ctx = None
        if tls is not None:
            ctx, created = tls
            if created:
                # The token may have crossed the network in the clear before: phones pair again over HTTPS.
                self.tokens.rotate()
        self.scheme = "https" if ctx else "http"
        for port in self.ports:
            for family, host in ((socket.AF_INET6, "::"), (socket.AF_INET, "0.0.0.0")):
                try:
                    self.server = _Server((host, port), family, self, ctx)
                except OSError:
                    continue
                self.port = port
                threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.5},
                                 daemon=True).start()
                self._advertise()
                return port
        return 0

    def stop(self, wait=False):
        """Stops it; with [wait], returns once the port is free again (for a restart on the same port)."""
        server, self.server, self.port = self.server, None, 0
        if server is None:
            return
        with self._lock:
            socks = list(self._trackpads)
        for s in socks:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if wait:
            server.shutdown()
            server.server_close()
        else:
            threading.Thread(target=lambda: (server.shutdown(), server.server_close()), daemon=True).start()
        if self._advertiser:
            self._advertiser.terminate()
            try:
                self._advertiser.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._advertiser.kill()
            self._advertiser = None

    def _advertise(self):
        """Announces the server on the home network through Avahi, if installed. Nothing depends on it."""
        prog = shutil.which("avahi-publish-service")
        if not prog:
            return
        try:
            self._advertiser = subprocess.Popen([prog, "Sofa TV Browser", "_http._tcp", str(self.port)],
                                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL)
        except OSError:
            self._advertiser = None

    def dispatch(self, name, *args):
        self._dispatch(getattr(self.actions, name), *args)

    def admit(self, remote, token):
        """None to go on, or (code, body, delay) to refuse with."""
        ip = _plain(remote)
        now = time.monotonic()
        with self._lock:
            f = self._failures.get(ip)
            if f and f[2] > now:
                return 429, "Too many attempts. Try again later.", 0
            if not self.tokens.matches(token):
                if not f or now - f[1] > FAIL_WINDOW_S:
                    f = [0, now, 0.0]
                    self._failures[ip] = f
                f[0] += 1
                if f[0] >= MAX_FAILURES:
                    f[0], f[2] = 0, now + BLOCK_S
                return 403, EXPIRED_PAGE, 0.3
            self._failures.pop(ip, None)
        return None

    def take_trackpad(self, sock) -> bool:
        with self._lock:
            if len(self._trackpads) >= MAX_SOCKETS:
                return False
            self._trackpads.add(sock)
            return True

    def drop_trackpad(self, sock):
        with self._lock:
            self._trackpads.discard(sock)

    def url(self):
        ip = lan_address()
        if not ip or not self.port:
            return None
        return f"{self.scheme}://{ip}:{self.port}/?t={self.tokens.token}"
