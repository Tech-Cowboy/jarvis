"""Login for the dashboard when it is reachable from outside the Mac.

Two rules, no exceptions:

* No DASHBOARD_PASSWORD  ->  only the Mac itself may use the dashboard. Anything that arrives
  through a proxy or tunnel (Cloudflare sets Cf-Connecting-Ip / X-Forwarded-For) or from a
  non-loopback address is refused with a note on how to enable remote access.
* DASHBOARD_PASSWORD set ->  every page, API call and WebSocket needs a session cookie, which
  the login page issues. Sessions are HMAC-signed (stdlib only), last DASHBOARD_SESSION_DAYS,
  and every one of them is invalidated by changing the password (the password is part of the key).

The signing secret is DASHBOARD_SECRET, or a random one kept in ~/.daxton/dashboard.secret (mode 600).
Failed logins are throttled per client address.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

COOKIE = "daxton_session"
PROXY_HEADERS = ("cf-connecting-ip", "cf-ray", "x-forwarded-for", "forwarded", "x-real-ip")
PUBLIC_PATHS = {"/login", "/logout", "/favicon.ico", "/healthz"}
MAX_FAILURES = 5          # free attempts before the throttle kicks in
LOCK_SECONDS = 30.0       # first lockout; doubles with every further failure
LOCK_MAX_SECONDS = 900.0
# Starlette's TestClient reports its peer as "testclient"; it never appears in real traffic.
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def load_or_create_secret(path: Path) -> bytes:
    """A 32-byte random secret persisted with owner-only permissions; created on first use."""
    try:
        if path.is_file():
            data = path.read_text(encoding="utf-8").strip()
            if len(data) >= 32:
                return data.encode()
    except OSError:
        pass
    value = secrets.token_hex(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        pass  # a fresh secret per run still works, sessions just end with the process
    return value.encode()


def is_local_address(host: str | None) -> bool:
    if host is None or host in _LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _origin_host(value: str) -> str:
    """'https://daxton.example.com:443' -> 'daxton.example.com'; a bare host stays as is."""
    if "://" not in value:
        value = "//" + value
    return (urlsplit(value).hostname or "").lower()


class DashboardAuth:
    def __init__(self, password: str = "", secret: bytes = b"", session_days: int = 30, public_hostname: str = "",
                 clock=time.time):
        self.password = password or ""
        self.session_days = max(1, int(session_days))
        self.public_hostname = (public_hostname or "").strip().lower()
        self._clock = clock
        # the password is part of the key: change it and every session is out
        self._key = hashlib.sha256((secret or b"") + b"\x00" + self.password.encode()).digest()
        self._failures: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ policy
    @property
    def enabled(self) -> bool:
        return bool(self.password)

    def is_local(self, conn) -> bool:
        """True for the Mac's own browser: a loopback peer and no proxy/tunnel headers."""
        client = getattr(conn, "client", None)
        host = getattr(client, "host", None) if client is not None else None
        if not is_local_address(host):
            return False
        headers = getattr(conn, "headers", {}) or {}
        return not any(h in headers for h in PROXY_HEADERS)

    def verdict(self, conn) -> str:
        """'ok' (serve it), 'login' (needs the login page) or 'local_only' (remote but no password configured)."""
        if not self.enabled:
            return "ok" if self.is_local(conn) else "local_only"
        cookies = getattr(conn, "cookies", {}) or {}
        return "ok" if self.verify_session(cookies.get(COOKIE, "")) else "login"

    def origin_ok(self, conn) -> bool:
        """For WebSockets and POSTs: when a browser sends Origin, it must be this dashboard's own host."""
        headers = getattr(conn, "headers", {}) or {}
        origin = headers.get("origin")
        if not origin:
            return True  # not a browser; cookies cannot be forged cross-site without one
        allowed = {_origin_host(headers.get("host", ""))}
        if self.public_hostname:
            allowed.add(self.public_hostname)
        allowed.discard("")
        return _origin_host(origin) in allowed

    # ---------------------------------------------------------- sessions
    def issue_session(self) -> str:
        expires = int(self._clock() + self.session_days * 86400)
        payload = f"{expires}.{secrets.token_hex(8)}"
        return f"{payload}.{self._sign(payload)}"

    def verify_session(self, value: str) -> bool:
        if not self.enabled or not value or value.count(".") != 2:
            return False
        expires, nonce, sig = value.split(".")
        if not hmac.compare_digest(sig, self._sign(f"{expires}.{nonce}")):
            return False
        try:
            return int(expires) > self._clock()
        except ValueError:
            return False

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def cookie_max_age(self) -> int:
        return self.session_days * 86400

    # ------------------------------------------------------------ login
    def locked_for(self, client: str) -> float:
        """Seconds until this client may try again (0 when it may try now)."""
        with self._lock:
            count, until = self._failures.get(client, (0, 0.0))
        return max(0.0, until - self._clock())

    def check_password(self, given: str, client: str = "") -> bool:
        """Constant-time compare with a per-client lockout after repeated failures."""
        if self.locked_for(client) > 0:
            return False
        ok = self.enabled and hmac.compare_digest(given.encode(), self.password.encode())
        with self._lock:
            if ok:
                self._failures.pop(client, None)
            else:
                count, _until = self._failures.get(client, (0, 0.0))
                count += 1
                lock = 0.0
                if count >= MAX_FAILURES:
                    lock = min(LOCK_MAX_SECONDS, LOCK_SECONDS * (2 ** (count - MAX_FAILURES)))
                self._failures[client] = (count, self._clock() + lock)
        return ok


def safe_next(path: str | None) -> str:
    """Only same-site relative paths may be used as a post-login redirect."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path
