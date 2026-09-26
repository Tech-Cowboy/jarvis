"""A read-only client for an Odoo database over XML-RPC (standard library only).

Credentials come from the environment (`ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_API_KEY`) or from a JSON
file named by `ODOO_CREDENTIALS_PATH` with the keys `url`, `db`, `username`, `api_key`: the same file the
company's other tools read, so the key lives in one place. The client refuses every method that is not a read,
so nothing that goes through it can create, change or delete a record, whatever the API key would allow.
"""

from __future__ import annotations

import json
import logging
import xmlrpc.client
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

READ_METHODS = frozenset({"search_read", "search_count", "read_group", "fields_get", "read", "name_search", "search",
                          "name_get"})
ODOO_DT = "%Y-%m-%d %H:%M:%S"
CREDENTIAL_KEYS = ("url", "db", "username", "api_key")


class OdooError(RuntimeError):
    """A failed call, in words a person can hear."""


class OdooNotConfigured(OdooError):
    """No credentials in the environment or the credentials file."""


def credentials_from(settings) -> dict[str, str]:
    """url, db, username and api_key from the settings' env quartet, else from the credentials file.

    Raises OdooNotConfigured when neither is complete. The values are never logged.
    """
    env = {"url": settings.odoo_url, "db": settings.odoo_db, "username": settings.odoo_username,
           "api_key": settings.odoo_api_key}
    if all(env.values()):
        return env
    path = settings.odoo_credentials_path
    if path:
        p = Path(path).expanduser()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise OdooNotConfigured(f"the credentials file {p} does not exist")
        except (OSError, ValueError) as e:
            raise OdooNotConfigured(f"the credentials file {p} cannot be read: {e.__class__.__name__}")
        creds = {k: str(data.get(k) or env.get(k) or "") for k in CREDENTIAL_KEYS}
        missing = [k for k in CREDENTIAL_KEYS if not creds[k]]
        if missing:
            raise OdooNotConfigured(f"the credentials file {p} lacks {', '.join(missing)}")
        return creds
    raise OdooNotConfigured("set ODOO_URL, ODOO_DB, ODOO_USERNAME and ODOO_API_KEY, or ODOO_CREDENTIALS_PATH, in .env")


def configured(settings) -> bool:
    """Is there a complete set of credentials to try? (Does not connect.)"""
    try:
        credentials_from(settings)
        return True
    except OdooNotConfigured:
        return False


class _SafeTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout: float):
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self.timeout
        return conn


class _PlainTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: float):
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self.timeout
        return conn


def _fault_text(fault: xmlrpc.client.Fault) -> str:
    """Odoo puts a whole traceback in faultString; the last non-empty line is the message."""
    lines = [ln.strip() for ln in str(fault.faultString).splitlines() if ln.strip()]
    text = lines[-1] if lines else str(fault.faultString)
    return text[:300]


class OdooReader:
    """Reads from one Odoo database as one user. Every call goes through `call`, which allows reads only."""

    def __init__(self, url: str, db: str, username: str, api_key: str, timeout: float = 25.0,
                 tz: str = "", proxies: tuple[Any, Any] | None = None):
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self._api_key = api_key
        self.timeout = timeout
        self.tz = ZoneInfo(tz) if tz else datetime.now().astimezone().tzinfo
        self._uid: int | None = None
        if proxies is not None:  # tests
            self.common, self.models = proxies
        else:
            secure = urlparse(self.url).scheme == "https"
            transport = _SafeTransport(timeout) if secure else _PlainTransport(timeout)
            self.common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", transport=transport, allow_none=True)
            self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", transport=transport, allow_none=True)

    @classmethod
    def from_settings(cls, settings) -> "OdooReader":
        creds = credentials_from(settings)
        return cls(creds["url"], creds["db"], creds["username"], creds["api_key"], tz=settings.business_tz)

    @property
    def host(self) -> str:
        return urlparse(self.url).netloc or self.url

    # ------------------------------------------------------------------ the wire
    @property
    def uid(self) -> int:
        if self._uid is None:
            try:
                uid = self.common.authenticate(self.db, self.username, self._api_key, {})
            except xmlrpc.client.Fault as e:
                raise OdooError(f"login failed: {_fault_text(e)}")
            except (OSError, xmlrpc.client.ProtocolError, xmlrpc.client.ResponseError) as e:
                raise OdooError(f"cannot reach {self.host}: {e}")
            if not uid:
                raise OdooError(f"{self.host} refused the login for {self.username} (wrong database, user or API key)")
            self._uid = int(uid)
        return self._uid

    def call(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if method not in READ_METHODS:
            raise OdooError(f"{method} on {model} is not a read; this connection is read-only")
        try:
            return self.models.execute_kw(self.db, self.uid, self._api_key, model, method, list(args), kwargs)
        except xmlrpc.client.Fault as e:
            raise OdooError(f"{model}.{method}: {_fault_text(e)}")
        except (OSError, xmlrpc.client.ProtocolError, xmlrpc.client.ResponseError) as e:
            raise OdooError(f"cannot reach {self.host}: {e}")

    def search_read(self, model: str, domain: list, fields: list[str], limit: int = 80, order: str = "",
                    offset: int = 0) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"fields": fields, "limit": limit}
        if order:
            kwargs["order"] = order
        if offset:
            kwargs["offset"] = offset
        return self.call(model, "search_read", domain, **kwargs)

    def search_count(self, model: str, domain: list) -> int:
        return int(self.call(model, "search_count", domain))

    def read_group(self, model: str, domain: list, fields: list[str], groupby: list[str], lazy: bool = False,
                   order: str = "") -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"lazy": lazy}
        if order:
            kwargs["orderby"] = order
        return self.call(model, "read_group", domain, fields, groupby, **kwargs)

    def fields_get(self, model: str, names: list[str] | None = None) -> dict[str, Any]:
        return self.call(model, "fields_get", names or [], attributes=["type", "string"])

    def whoami(self) -> dict[str, Any]:
        rows = self.search_read("res.users", [["id", "=", self.uid]], ["name", "login", "tz"], limit=1)
        info = rows[0] if rows else {"id": self.uid}
        try:
            info["server"] = self.common.version().get("server_version", "")
        except Exception:
            info["server"] = ""
        return info

    # ------------------------------------------------------------------ time
    def to_local(self, value: str | None) -> datetime | None:
        """An Odoo datetime string (UTC, naive) as an aware local datetime."""
        if not value:
            return None
        try:
            return datetime.strptime(value[:19], ODOO_DT).replace(tzinfo=timezone.utc).astimezone(self.tz)
        except ValueError:
            return None

    def utc_str(self, dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.tz)
        return dt.astimezone(timezone.utc).strftime(ODOO_DT)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def day_bounds(self, day: date, days: int = 1) -> tuple[str, str]:
        """UTC strings for [local midnight, local midnight + days)."""
        start = datetime.combine(day, dtime.min, tzinfo=self.tz)
        return self.utc_str(start), self.utc_str(start + timedelta(days=days))
