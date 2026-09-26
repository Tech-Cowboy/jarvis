"""Business skills: what the company's system of record says right now (read-only).

Bookings, customers, leads, sales, the inbox and reminders are read live from the business system, so a
number Daxton says is the number the system holds at that moment, never one remembered from an earlier
conversation. Nothing here can change a record, send a message or move money: the client underneath
(`daxton/business/odoo.py`) refuses every method that is not a read. In speech the system is "the business
system" or `BUSINESS_NAME`'s system; the vendor's name is never said.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from ..business.odoo import OdooError, OdooNotConfigured, OdooReader
from .context import SkillContext
from .registry import skill

log = logging.getLogger(__name__)

BUSINESS_SKILLS = ["bookings", "find_customer", "recent_leads", "sales_summary", "inbox", "reminders", "price_check"]
MAX_LINES = 28
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "thirty": "30",
                "fifteen": "15", "forty five": "45", "sixty": "60", "ninety": "90", "half hour": "30", "half-hour": "30"}
MODEL_LABELS = {"crm.lead": "lead", "sale.order": "order", "res.partner": "contact", "project.task": "task",
                "helpdesk.ticket": "ticket", "calendar.event": "booking", "account.move": "invoice",
                "hr.applicant": "applicant", "event.registration": "event registration", "mail.channel": "channel"}
STOP_WORDS = {"the", "a", "an", "for", "of", "on", "in", "at", "to", "and", "with", "per", "by", "our", "my", "your"}
CURRENCY_SYMBOLS = {"USD": "$", "CAD": "$", "AUD": "$", "NZD": "$", "EUR": "€", "GBP": "£"}

_lock = threading.Lock()
_reader: OdooReader | None = None
_reader_for: int | None = None
_caps: dict[str, Any] = {}


# ------------------------------------------------------------------ the connection
def reader_for(settings) -> OdooReader:
    """One connection per settings object; tests install a fake with set_reader()."""
    global _reader, _reader_for
    with _lock:
        if _reader is None or _reader_for != id(settings):
            _reader = OdooReader.from_settings(settings)
            _reader_for = id(settings)
            _caps.clear()
        return _reader


def set_reader(reader: Any, settings: Any = None) -> None:
    global _reader, _reader_for
    with _lock:
        _reader = reader
        _reader_for = id(settings) if settings is not None else None
        _caps.clear()


def _run(ctx: SkillContext | None, fn) -> str:
    if ctx is None:
        return "Error: no settings."
    try:
        return fn(reader_for(ctx.settings))
    except OdooNotConfigured as e:
        return f"The business system is not connected: {e}."
    except OdooError as e:
        return f"The business system did not answer: {e}"


def _has_field(r: OdooReader, model: str, name: str) -> bool:
    key = f"{model}.{name}"
    if key not in _caps:
        try:
            _caps[key] = name in r.fields_get(model, [name])
        except OdooError:
            _caps[key] = False
    return _caps[key]


def _has_model(r: OdooReader, model: str) -> bool:
    key = f"model:{model}"
    if key not in _caps:
        try:
            _caps[key] = r.search_count("ir.model", [["model", "=", model]]) > 0
        except OdooError:
            _caps[key] = False
    return _caps[key]


def _currency(r: OdooReader) -> tuple[str, str]:
    """(symbol, code) of the main company's currency."""
    if "currency" not in _caps:
        code = "USD"
        try:
            rows = r.search_read("res.company", [], ["currency_id"], limit=1, order="id asc")
            if rows and rows[0].get("currency_id"):
                code = rows[0]["currency_id"][1]
        except OdooError:
            pass
        _caps["currency"] = (CURRENCY_SYMBOLS.get(code, ""), code)
    return _caps["currency"]


def _money(r: OdooReader, amount: float) -> str:
    symbol, code = _currency(r)
    text = f"{amount:,.2f}"
    return f"{symbol}{text}" if symbol else f"{text} {code}"


# ------------------------------------------------------------------ words and dates
def clean(text: Any) -> str:
    """Record names as speech: no em dashes, no pipes, single spaces."""
    s = str(text or "").replace("—", "-").replace("–", "-").replace(" | ", ", ").replace("|", ",")
    return re.sub(r"\s+", " ", s).strip()


def _query_words(query: str) -> list[str]:
    """The words worth matching: number words as digits, plurals trimmed, filler dropped."""
    q = " " + query.lower().strip() + " "
    for word, digit in NUMBER_WORDS.items():
        q = q.replace(f" {word} ", f" {digit} ")
    words = [w for w in re.split(r"[\s,]+", q) if (len(w) > 1 or w.isdigit()) and w not in STOP_WORDS]
    return [w[:-1] if w.endswith("s") and len(w) > 4 else w for w in words]  # lessons -> lesson (ilike)


def _words_domain(field: str, query: str) -> list:
    """AND of ilike on each word of the query ('one hour lesson' finds 'Horsemanship Lessons - 1 Hour')."""
    words = _query_words(query)
    if not words:
        return [[field, "ilike", query.strip()]]
    return ["&"] * (len(words) - 1) + [[field, "ilike", w] for w in words]


def _any_words_domain(field: str, words: list[str]) -> list:
    """OR of ilike on the words (ranked afterwards by how many matched)."""
    if not words:
        return [[field, "ilike", ""]]
    return ["|"] * (len(words) - 1) + [[field, "ilike", w] for w in words]


def parse_day(text: str, today: date) -> tuple[date, int] | None:
    """(first day, number of days) for 'today', 'tomorrow', a weekday, 'week', 'weekend' or a date; None if unclear."""
    t = re.sub(r"\s+", " ", (text or "today").strip().lower())
    t = t.replace("the day after tomorrow", "day after tomorrow")
    if t in ("", "today", "now", "tonight", "this morning", "this afternoon"):
        return today, 1
    if t == "tomorrow":
        return today + timedelta(days=1), 1
    if t == "yesterday":
        return today - timedelta(days=1), 1
    if t == "day after tomorrow":
        return today + timedelta(days=2), 1
    if t in ("week", "this week", "the week", "next 7 days", "next seven days", "coming week", "7 days"):
        return today, 7
    if t == "next week":
        return today + timedelta(days=(7 - today.weekday()) % 7 or 7), 7
    if t in ("weekend", "this weekend", "the weekend"):
        return today + timedelta(days=(5 - today.weekday()) % 7), 2
    if t == "next weekend":
        return today + timedelta(days=((5 - today.weekday()) % 7) + 7), 2
    for i, name in enumerate(WEEKDAYS):
        forms = {name, name[:3], f"this {name}", f"on {name}", f"next {name}", f"{name}s"}
        if t in forms:
            delta = (i - today.weekday()) % 7
            if t.startswith("next ") and delta == 0:
                delta = 7
            return today + timedelta(days=delta), 1
    try:
        return date.fromisoformat(t), 1
    except ValueError:
        pass
    m = re.match(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$", t)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            parsed = date(year, month, day)
        except ValueError:
            return None
        if not m.group(3) and parsed < today - timedelta(days=180):
            parsed = parsed.replace(year=year + 1)
        return parsed, 1
    t2 = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", t).replace(",", "")
    for fmt in ("%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y", "%B %d", "%b %d", "%d %B", "%d %b"):
        try:
            parsed = datetime.strptime(t2, fmt).date()
        except ValueError:
            continue
        if "%Y" not in fmt:
            parsed = parsed.replace(year=today.year)
            if parsed < today - timedelta(days=180):
                parsed = parsed.replace(year=today.year + 1)
        return parsed, 1
    return None


def _day_phrase(day: date, today: date) -> str:
    text = day.strftime("%A, %B ") + str(day.day)
    if day == today:
        return f"today, {text}"
    if day == today + timedelta(days=1):
        return f"tomorrow, {text}"
    if day == today - timedelta(days=1):
        return f"yesterday, {text}"
    return text


def _clock(dt: datetime | None) -> str:
    if dt is None:
        return "?"
    return dt.strftime("%I:%M %p").lstrip("0")


def _when(dt: datetime | None, now: datetime) -> str:
    """'today 3:30 PM', 'tomorrow 9:00 AM', 'Sat Sep 27 9:00 AM'."""
    if dt is None:
        return "sometime"
    day = dt.date()
    if day == now.date():
        return f"today {_clock(dt)}"
    if day == now.date() + timedelta(days=1):
        return f"tomorrow {_clock(dt)}"
    if day == now.date() - timedelta(days=1):
        return f"yesterday {_clock(dt)}"
    return f"{dt.strftime('%a %b')} {day.day} {_clock(dt)}"


def _month_day(value: str | None, r: OdooReader) -> str:
    dt = r.to_local(value)
    if dt is None:
        return "an unknown date"
    text = dt.strftime("%B ") + str(dt.day)
    return text if dt.year == r.now().year else f"{text}, {dt.year}"


def _sender(email_from: Any) -> str:
    """'Jane Doe <jane@x.com>' -> 'Jane Doe'; a bare address stays an address."""
    s = clean(email_from)
    m = re.match(r'^"?([^"<]+?)"?\s*<[^>]+>$', s)
    return m.group(1).strip() if m else s


def _limit(lines: list[str], head: int = MAX_LINES) -> list[str]:
    if len(lines) <= head:
        return lines
    return lines[:head] + [f"... and {len(lines) - head} more"]


def _many2one(value: Any) -> str:
    return clean(value[1]) if isinstance(value, (list, tuple)) and len(value) > 1 else ""


# ------------------------------------------------------------------ bookings
def _customer_labels(r: OdooReader, rows: list[dict]) -> dict[int, str]:
    """A speakable customer label per event: the name before ' - ' when the booking is named that way, else the
    attendees' names (one batch read)."""
    labels: dict[int, str] = {}
    need: dict[int, list[int]] = {}
    for row in rows:
        name = clean(row.get("name"))
        kind = _many2one(row.get("appointment_type_id")).lower()
        prefix, sep, rest = name.partition(" - ")
        prefix = prefix.strip()
        named = bool(sep) and prefix and not kind.startswith(prefix.lower()) and (
            not kind or kind[:8] in rest.lower() or rest.lower()[:8] in kind)
        if named:
            labels[row["id"]] = prefix
        elif row.get("partner_ids"):
            need[row["id"]] = list(row["partner_ids"])[:4]
        else:
            labels[row["id"]] = ""
    ids = sorted({pid for pids in need.values() for pid in pids})
    names: dict[int, str] = {}
    if ids:
        try:
            for p in r.search_read("res.partner", [["id", "in", ids]], ["name"], limit=len(ids)):
                names[p["id"]] = clean(p["name"])
        except OdooError:
            pass
    for event_id, pids in need.items():
        labels[event_id] = ", ".join(n for n in (names.get(pid, "") for pid in pids) if n)
    return labels


def _bookings_rows(r: OdooReader, lo: str, hi: str, who: str) -> list[dict]:
    fields = ["name", "start", "stop", "allday", "partner_ids"]
    has_types = _has_field(r, "calendar.event", "appointment_type_id")
    domain: list = [["start", ">=", lo], ["start", "<", hi]]
    if has_types:
        fields += ["appointment_type_id", "appointment_status"]
        domain.append(["appointment_type_id", "!=", False])
        if _has_field(r, "calendar.event", "total_capacity_reserved"):
            fields.append("total_capacity_reserved")
    if who:
        domain += _words_domain("name", who)
    return r.search_read("calendar.event", domain, fields, limit=500, order="start asc")


def _riders(row: dict) -> int:
    return int(row.get("total_capacity_reserved") or 0) or 1


@skill()
def bookings(
    day: Annotated[str, "Which day: 'today' (default), 'tomorrow', a weekday, a date such as 'September 27' or "
                        "'2026-09-27', 'weekend', or 'week' for the next seven days"] = "today",
    who: Annotated[str, "Optional: only bookings whose name contains this customer's name"] = "",
    ctx: SkillContext = None,
) -> str:
    """Bookings and appointments in the business system for a day, the weekend or the coming week: what is on, when, for whom, how many riders, and which were cancelled or missed."""

    def go(r: OdooReader) -> str:
        today = r.now().date()
        parsed = parse_day(day, today)
        if parsed is None:
            return f"I don't understand the day '{day}'. Try today, tomorrow, a weekday, or a date like September 27."
        first, ndays = parsed
        lo, hi = r.day_bounds(first, ndays)
        rows = _bookings_rows(r, lo, hi, who)
        for row in rows:
            row["_start"] = r.to_local(row.get("start"))
        if ndays > 1:
            return _week_view(rows, first, ndays, today, who)
        return _day_view(r, rows, first, today, who)

    return _run(ctx, go)


def _week_view(rows: list[dict], first: date, ndays: int, today: date, who: str) -> str:
    if not rows:
        span = f"{_day_phrase(first, today)} through {(first + timedelta(days=ndays - 1)).strftime('%A, %B ')}{(first + timedelta(days=ndays - 1)).day}"
        return f"No bookings{' for ' + who if who else ''} from {span}."
    per_day: dict[date, list[dict]] = {}
    for row in rows:
        if row["_start"] is not None:
            per_day.setdefault(row["_start"].date(), []).append(row)
    total_riders = sum(_riders(x) for x in rows if x.get("appointment_status") not in ("cancelled", "no_show"))
    lines = [f"{len(rows)} bookings, {total_riders} riders, over {ndays} days{' for ' + who if who else ''}:"]
    for d in sorted(per_day):
        items = per_day[d]
        kinds: dict[str, int] = {}
        for row in items:
            if row.get("appointment_status") in ("cancelled", "no_show"):
                continue
            kinds[_many2one(row.get("appointment_type_id")) or "appointment"] = kinds.get(
                _many2one(row.get("appointment_type_id")) or "appointment", 0) + _riders(row)
        kinds_text = ", ".join(f"{k} {n}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])[:5])
        day_riders = sum(kinds.values())
        lines.append(f"{d.strftime('%a %b')} {d.day}: {len(items)} bookings, {day_riders} riders ({kinds_text})")
    return "\n".join(_limit(lines))


def _day_view(r: OdooReader, rows: list[dict], day: date, today: date, who: str) -> str:
    phrase = _day_phrase(day, today)
    if not rows:
        return f"No bookings {phrase}{' for ' + who if who else ''}."
    labels = _customer_labels(r, rows)
    groups: dict[tuple[str, str], list[dict]] = {}
    status: dict[str, int] = {}
    for row in rows:
        st = row.get("appointment_status") or "booked"
        status[st] = status.get(st, 0) + 1
        key = (_clock(row["_start"]), _many2one(row.get("appointment_type_id")) or clean(row.get("name")))
        groups.setdefault(key, []).append(row)
    live = [x for x in rows if x.get("appointment_status") not in ("cancelled", "no_show")]
    riders = sum(_riders(x) for x in live)
    head = f"{phrase[0].upper()}{phrase[1:]}: {len(rows)} bookings, {riders} riders"
    extras = [f"{n} {s.replace('_', ' ')}" for s, n in status.items() if s != "booked"]
    head += f" ({', '.join(extras)})." if extras else "."
    lines = [head]
    for (clock, kind), items in sorted(groups.items(), key=lambda kv: kv[1][0]["_start"] or datetime.max.replace(tzinfo=r.tz)):
        people: dict[tuple[str, str], int] = {}  # (label, flag) -> riders; the same person twice is one entry
        for row in items:
            label = labels.get(row["id"], "") or "unnamed"
            st = row.get("appointment_status")
            flag = f" ({st.replace('_', ' ')})" if st in ("cancelled", "no_show") else ""
            people[(label, flag)] = people.get((label, flag), 0) + _riders(row)
        parts = [f"{label}{f' x{n}' if n > 1 else ''}{flag}" for (label, flag), n in people.items()]
        group_riders = sum(_riders(x) for x in items if x.get("appointment_status") not in ("cancelled", "no_show"))
        count = f"{group_riders} riders: " if len(items) > 1 or group_riders > 1 else ""
        lines.append(f"{clock} {kind}: {count}{', '.join(parts[:8])}{f', and {len(parts) - 8} more' if len(parts) > 8 else ''}")
    return "\n".join(_limit(lines))


# ------------------------------------------------------------------ customers
@skill()
def find_customer(
    query: Annotated[str, "The customer's name, email address or phone number (part of it is fine)"],
    ctx: SkillContext = None,
) -> str:
    """Look a customer up in the business system: contact details, how long they have been a customer, their orders, their next booking and last visit."""

    def go(r: OdooReader) -> str:
        q = query.strip()
        if len(q) < 2:
            return "Give me a name, an email address or a phone number to look up."
        fields = ["name", "email", "phone", "city", "create_date", "customer_rank", "is_company", "parent_id"]
        has_orders = _has_field(r, "res.partner", "sale_order_count")
        if has_orders:
            fields.append("sale_order_count")
        digits = re.sub(r"\D", "", q)
        if len(digits) >= 7 and len(digits) >= len(q) - 6:
            # phones are stored formatted in all sorts of ways; the last four digits survive every format
            rows = r.search_read("res.partner", [["phone", "ilike", digits[-4:]]], fields, limit=40,
                                 order="customer_rank desc, id desc")
            rows = [x for x in rows if re.sub(r"\D", "", x.get("phone") or "").endswith(digits[-7:])][:5]
        else:
            domain = ["|", ["email", "ilike", q]] + _words_domain("name", q)
            rows = r.search_read("res.partner", domain, fields, limit=5, order="customer_rank desc, id desc")
        if not rows:
            return f"No customer matches '{q}' in {ctx.settings.business_label}'s system."
        top = rows[0]
        pid = top["id"]
        now = r.now()
        bits = [clean(top["name"])]
        if top.get("parent_id"):
            bits[0] += f" of {_many2one(top['parent_id'])}"
        if top.get("create_date"):
            bits.append(f"customer since {_month_day(top.get('create_date'), r)}")
        if top.get("email"):
            bits.append(top["email"])
        if top.get("phone"):
            bits.append(clean(top["phone"]))
        if top.get("city"):
            bits.append(clean(top["city"]))
        lines = [", ".join(bits) + "."]
        if has_orders and _has_model(r, "sale.order"):
            n = int(top.get("sale_order_count") or 0)
            last = r.search_read("sale.order", [["partner_id", "=", pid], ["state", "in", ["sale", "done"]]],
                                 ["name", "date_order", "amount_total"], limit=1, order="date_order desc")
            if last:
                o = last[0]
                lines.append(f"{n} orders; last {o['name']} on {_month_day(o.get('date_order'), r)} for {_money(r, o.get('amount_total') or 0)}.")
            else:
                lines.append("No confirmed orders.")
        nxt = r.search_read("calendar.event", [["partner_ids", "in", [pid]], ["start", ">=", r.utc_str(now)]],
                            ["name", "start", "appointment_type_id"], limit=1, order="start asc")
        prev = r.search_read("calendar.event", [["partner_ids", "in", [pid]], ["start", "<", r.utc_str(now)]],
                             ["name", "start", "appointment_type_id"], limit=1, order="start desc")
        if nxt:
            e = nxt[0]
            lines.append(f"Next booking: {_when(r.to_local(e.get('start')), now)}, {_many2one(e.get('appointment_type_id')) or clean(e.get('name'))}.")
        if prev:
            e = prev[0]
            lines.append(f"Last visit: {_month_day(e.get('start'), r)}, {_many2one(e.get('appointment_type_id')) or clean(e.get('name'))}.")
        if not nxt and not prev:
            lines.append("No bookings on record.")
        others = [clean(x["name"]) for x in rows[1:]]
        if others:
            lines.append(f"Other matches: {', '.join(others)}.")
        return "\n".join(lines)

    return _run(ctx, go)


# ------------------------------------------------------------------ leads
@skill()
def recent_leads(
    days: Annotated[int, "How many days back to look (default 1)"] = 1,
    ctx: SkillContext = None,
) -> str:
    """New leads and inquiries in the business system over the last days (web forms, calls the phone assistant logged, referrals), with their stage and where they came from."""

    def go(r: OdooReader) -> str:
        if not _has_model(r, "crm.lead"):
            return "The business system has no CRM."
        span = max(1, min(int(days or 1), 90))
        now = r.now()
        since = r.utc_str(now - timedelta(days=span))
        fields = ["name", "contact_name", "stage_id", "create_date", "expected_revenue"]
        for extra in ("source_id", "medium_id"):
            if _has_field(r, "crm.lead", extra):
                fields.append(extra)
        rows = r.search_read("crm.lead", [["create_date", ">=", since]], fields, limit=60, order="create_date desc")
        period = "the last day" if span == 1 else f"the last {span} days"
        if not rows:
            return f"No new leads in {period}."
        stages: dict[str, int] = {}
        for row in rows:
            st = _many2one(row.get("stage_id")) or "no stage"
            stages[st] = stages.get(st, 0) + 1
        lines = [f"{len(rows)} new leads in {period}: " + ", ".join(f"{n} {s}" for s, n in stages.items()) + "."]
        for row in rows:
            via = ", ".join(v for v in (_many2one(row.get("source_id")), _many2one(row.get("medium_id"))) if v)
            who = clean(row.get("contact_name"))
            name = clean(row.get("name"))
            desc = name if not who or who.lower() in name.lower() else f"{name} ({who})"
            lines.append(f"{_when(r.to_local(row.get('create_date')), now)}: {desc}, {_many2one(row.get('stage_id')) or 'no stage'}"
                         + (f", via {via}" if via else ""))
        return "\n".join(_limit(lines))

    return _run(ctx, go)


# ------------------------------------------------------------------ sales
def _period(text: str, today: date) -> tuple[date, date, str] | None:
    """(first day, day after the last, phrase) for today, yesterday, week, month, year, last ..., or a date."""
    t = re.sub(r"\s+", " ", (text or "today").strip().lower())
    if t in ("", "today"):
        return today, today + timedelta(days=1), "today"
    if t == "yesterday":
        return today - timedelta(days=1), today, "yesterday"
    if t in ("week", "this week"):
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=7), "this week"
    if t == "last week":
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=7), "last week"
    if t in ("month", "this month"):
        start = today.replace(day=1)
        return start, (start + timedelta(days=32)).replace(day=1), "this month"
    if t == "last month":
        end = today.replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
        return start, end, "last month"
    if t in ("year", "this year"):
        return today.replace(month=1, day=1), today.replace(year=today.year + 1, month=1, day=1), "this year"
    if t == "last year":
        return today.replace(year=today.year - 1, month=1, day=1), today.replace(month=1, day=1), "last year"
    m = re.match(r"^(?:last |past )?(\d{1,3}) days?$", t)
    if m:
        n = int(m.group(1))
        return today - timedelta(days=n - 1), today + timedelta(days=1), f"the last {n} days"
    parsed = parse_day(t, today)
    if parsed:
        first, n = parsed
        return first, first + timedelta(days=n), _day_phrase(first, today) if n == 1 else f"{n} days from {_day_phrase(first, today)}"
    return None


def _sum_count(r: OdooReader, model: str, domain: list, amount_field: str) -> tuple[float, int]:
    """Total and count, via read_group when the server offers it, else by reading the rows."""
    try:
        groups = r.read_group(model, domain, [f"{amount_field}:sum"], [], lazy=False)
        if groups:
            g = groups[0]
            count = g.get("__count", g.get(f"{model.replace('.', '_')}_count", 0))
            return float(g.get(amount_field) or 0.0), int(count or 0)
        return 0.0, 0
    except OdooError:
        rows = r.search_read(model, domain, [amount_field], limit=5000)
        return float(sum(float(x.get(amount_field) or 0) for x in rows)), len(rows)


@skill()
def sales_summary(
    period: Annotated[str, "'today' (default), 'yesterday', 'week', 'last week', 'month', 'last month', 'year', "
                           "'last 30 days', or a date"] = "today",
    ctx: SkillContext = None,
) -> str:
    """Sales in the business system for a period: confirmed orders and their total, point-of-sale takings, and what is still unpaid."""

    def go(r: OdooReader) -> str:
        today = r.now().date()
        p = _period(period, today)
        if p is None:
            return f"I don't understand the period '{period}'. Try today, this week, last month or a date."
        first, end, phrase = p
        lo, hi = r.utc_str(datetime.combine(first, datetime.min.time(), tzinfo=r.tz)), r.utc_str(
            datetime.combine(end, datetime.min.time(), tzinfo=r.tz))
        parts = []
        if _has_model(r, "sale.order"):
            total, n = _sum_count(r, "sale.order", [["state", "in", ["sale", "done"]], ["date_order", ">=", lo],
                                                    ["date_order", "<", hi]], "amount_total")
            parts.append(f"{n} confirmed orders for {_money(r, total)}" if n else "no confirmed orders")
        if _has_model(r, "pos.order"):
            total, n = _sum_count(r, "pos.order", [["state", "in", ["paid", "done", "invoiced"]], ["date_order", ">=", lo],
                                                   ["date_order", "<", hi]], "amount_total")
            parts.append(f"{n} point-of-sale orders for {_money(r, total)}" if n else "no point-of-sale orders")
        if not parts:
            return "The business system has no sales apps."
        lines = [f"Sales {phrase}: " + " and ".join(parts) + "."]
        if _has_model(r, "account.move"):
            due, n = _sum_count(r, "account.move", [["move_type", "=", "out_invoice"], ["state", "=", "posted"],
                                                    ["payment_state", "in", ["not_paid", "partial"]]], "amount_residual")
            lines.append(f"Unpaid invoices right now: {n} for {_money(r, due)}." if n else "No unpaid invoices.")
        return "\n".join(lines)

    return _run(ctx, go)


# ------------------------------------------------------------------ inbox and tickets
@skill()
def inbox(
    days: Annotated[int, "How many days back to look (default 1)"] = 1,
    ctx: SkillContext = None,
) -> str:
    """Recent incoming email in the business system (who wrote, about what, on which record) and the open support tickets."""

    def go(r: OdooReader) -> str:
        span = max(1, min(int(days or 1), 30))
        now = r.now()
        since = r.utc_str(now - timedelta(days=span))
        rows = r.search_read("mail.message", [["message_type", "=", "email"], ["date", ">=", since]],
                             ["subject", "email_from", "date", "model", "res_id", "record_name"], limit=40, order="date desc")
        seen: set[tuple[str, str]] = set()
        mails = []
        for row in rows:
            key = (clean(row.get("subject")), str(row.get("date"))[:16])
            if key in seen:
                continue
            seen.add(key)
            mails.append(row)
        period = "the last day" if span == 1 else f"the last {span} days"
        lines = [f"{len(mails)} incoming emails in {period}." if mails else f"No incoming email in {period}."]
        for row in mails:
            where = MODEL_LABELS.get(row.get("model") or "", row.get("model") or "")
            record = clean(row.get("record_name"))
            on = f", on the {where} {record}" if record and where else (f", on {where}" if where else "")
            lines.append(f"{_when(r.to_local(row.get('date')), now)}: {clean(row.get('subject')) or '(no subject)'} from {_sender(row.get('email_from')) or 'unknown'}{on}")
        if _has_model(r, "helpdesk.ticket"):
            try:
                open_count = r.search_count("helpdesk.ticket", [["stage_id.fold", "=", False]])
                newest = r.search_read("helpdesk.ticket", [["stage_id.fold", "=", False]], ["name", "stage_id", "create_date"],
                                       limit=3, order="create_date desc")
                if open_count:
                    tops = "; ".join(f"{clean(t['name'])} ({_many2one(t.get('stage_id'))})" for t in newest)
                    lines.append(f"Open support tickets: {open_count}. Newest: {tops}.")
                else:
                    lines.append("No open support tickets.")
            except OdooError:
                pass
        return "\n".join(_limit(lines))

    return _run(ctx, go)


# ------------------------------------------------------------------ reminders
@skill()
def reminders(ctx: SkillContext = None) -> str:
    """Overdue and due-today reminders and to-dos assigned to you in the business system (follow-ups, calls, vet and care dates), plus how many are due this week."""

    def go(r: OdooReader) -> str:
        today = r.now().date()
        fields = ["summary", "res_model", "res_name", "date_deadline", "activity_type_id"]
        rows = r.search_read("mail.activity", [["user_id", "=", r.uid], ["date_deadline", "<=", today.isoformat()]], fields,
                             limit=60, order="date_deadline asc")
        week = r.search_count("mail.activity", [["user_id", "=", r.uid], ["date_deadline", ">", today.isoformat()],
                                                ["date_deadline", "<=", (today + timedelta(days=7)).isoformat()]])
        if not rows:
            return f"Nothing overdue or due today. {week} due in the next seven days." if week else "Nothing due today or this week."
        overdue = sum(1 for x in rows if x.get("date_deadline") and x["date_deadline"] < today.isoformat())
        lines = [f"{len(rows)} due ({overdue} overdue), {week} more this week."]
        groups: dict[tuple[str, str, str], list[str]] = {}  # the same reminder on many records is one line
        for row in rows:
            what = clean(row.get("summary")) or _many2one(row.get("activity_type_id")) or "to-do"
            model = row.get("res_model") or ""
            where = MODEL_LABELS.get(model, model.split(".")[-1].replace("x_", "").replace("_", " ") if model else "")
            record = clean(row.get("res_name"))
            try:
                due = date.fromisoformat(str(row.get("date_deadline"))[:10])
                when = "today" if due == today else f"since {due.strftime('%b')} {due.day}" + ("" if due.year == today.year else f", {due.year}")
            except ValueError:
                when = ""
            if record and record.lower() in what.lower():
                template = re.sub(re.escape(record), "{}", what, count=1, flags=re.I)
            else:
                template = f"{what} on {{}}" if record else what
            groups.setdefault((template, where, when), []).append(record)
        for (template, where, when), records in groups.items():
            names = [x for x in records if x]
            shown = ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else "")
            text = template.format(shown) if "{}" in template else template
            if where and where.lower() not in text.lower():
                text += f" ({where})"
            lines.append(f"{text}, {when}".rstrip(", "))
        return "\n".join(_limit(lines))

    return _run(ctx, go)


# ------------------------------------------------------------------ prices
@skill()
def price_check(
    product: Annotated[str, "The product or service, e.g. 'beach ride' or 'one hour lesson'"],
    ctx: SkillContext = None,
) -> str:
    """The current list price of a product or service, read live from the business system. Never state a price that did not come from this tool."""

    def go(r: OdooReader) -> str:
        q = product.strip()
        if not q:
            return "Which product or service?"
        fields = ["name", "list_price"]
        has_taxes = _has_field(r, "product.template", "taxes_id")
        if has_taxes:
            fields.append("taxes_id")
        words = _query_words(q)
        rows = r.search_read("product.template", [["sale_ok", "=", True]] + _any_words_domain("name", words), fields,
                             limit=300, order="name asc")
        if not rows:
            return f"No product or service matching '{q}' is on sale in {ctx.settings.business_label}'s system."
        # the products naming every word first, then the rest; shorter names (the main products) before add-ons
        rows.sort(key=lambda row: (-sum(1 for w in words if w in clean(row["name"]).lower()), len(clean(row["name"]))))
        rows = rows[:6]
        tax_names: dict[int, str] = {}
        if has_taxes:
            ids = sorted({t for row in rows for t in (row.get("taxes_id") or [])})
            if ids:
                try:
                    for t in r.search_read("account.tax", [["id", "in", ids]], ["name"], limit=len(ids)):
                        tax_names[t["id"]] = clean(t["name"])
                except OdooError:
                    pass
        lines = []
        for row in rows:
            taxes = ", ".join(tax_names.get(t, "") for t in (row.get("taxes_id") or []) if tax_names.get(t))
            lines.append(f"{clean(row['name'])}: {_money(r, float(row.get('list_price') or 0))} list price"
                         + (f" plus {taxes}" if taxes else ""))
        head = "Live prices: " if len(lines) > 1 else "Live price: "
        return head + "\n".join(lines) + "\n(List prices from the system right now; a discount, membership or pricelist can change what a customer pays.)"

    return _run(ctx, go)


# ------------------------------------------------------------------ registration
SKILL_FUNCS = {f.__name__: f for f in (bookings, find_customer, recent_leads, sales_summary, inbox, reminders, price_check)}
assert list(SKILL_FUNCS) == BUSINESS_SKILLS


def ensure_registered(registry, configured: bool) -> None:
    """Business skills are tools only when a business system is configured (idempotent either way)."""
    for name in BUSINESS_SKILLS:
        if configured and registry.get(name) is None:
            registry.add(SKILL_FUNCS[name])
        elif not configured:
            registry.remove(name)
