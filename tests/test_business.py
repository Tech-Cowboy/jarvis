"""The business system: read-only client, credentials, and the skills over a fake database."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from daxton.business import odoo as odoo_mod
from daxton.business.odoo import OdooError, OdooNotConfigured, OdooReader, credentials_from
from daxton.config import Settings
from daxton.skills import business, load_default_skills
from daxton.skills.context import SkillContext
from daxton.skills.registry import SkillRegistry

TZ = "America/Los_Angeles"


# ------------------------------------------------------------------ a fake database
class FakeModels:
    """Answers execute_kw from canned rows; records every call."""

    def __init__(self, data: dict[str, list[dict]]):
        self.data = data
        self.calls: list[tuple] = []

    def execute_kw(self, db, uid, key, model, method, args, kwargs):
        self.calls.append((model, method, args, kwargs))
        if method == "fields_get":
            return {name: {"type": "char", "string": name} for name in (args[0] or []) if name in self.data.get(f"fields:{model}", [])}
        rows = self.data.get(model, [])
        if method == "search_count":
            if model == "ir.model":
                return 1 if any(m == args[0][0][2] for m in self.data.get("models", [])) else 0
            return len(rows)
        if method == "search_read":
            fields = kwargs.get("fields") or []
            out = [{k: r.get(k) for k in (["id"] + fields)} for r in self._filter(model, rows, args[0])]
            return out[: kwargs.get("limit", 80)]
        if method == "read_group":
            if model in self.data.get("no_read_group", []):
                raise odoo_mod.xmlrpc.client.Fault(1, "Traceback\nValueError: read_group is gone")
            field = args[1][0].split(":")[0]
            return [{field: sum(float(r.get(field) or 0) for r in rows), "__count": len(rows)}]
        raise AssertionError(f"unexpected {method}")

    def _filter(self, model, rows, domain):
        # only what the skills use: a name/email/phone ilike, an id-in, a partner filter; anything else passes
        out = rows
        for term in domain:
            if not isinstance(term, list) or len(term) != 3:
                continue
            f, op, v = term
            if op == "ilike" and f in ("name", "email", "phone", "subject"):
                out = [r for r in out if str(v).lower() in str(r.get(f) or "").lower()]
            elif op == "in" and f == "id":
                out = [r for r in out if r.get("id") in v]
            elif op == "in" and f == "partner_ids":
                out = [r for r in out if set(r.get("partner_ids") or []) & set(v)]
            elif op == "=" and f == "partner_id":
                out = [r for r in out if (r.get("partner_id") or [None])[0] == v]
        return out


class FakeCommon:
    def __init__(self, uid=2):
        self._uid = uid

    def authenticate(self, db, user, key, ctx):
        return self._uid if key == "good" else False

    def version(self):
        return {"server_version": "19.0+e"}


def make_reader(data: dict, uid=2, key="good") -> tuple[OdooReader, FakeModels]:
    models = FakeModels(data)
    r = OdooReader("https://x.example.com", "db", "user@example.com", key, tz=TZ, proxies=(FakeCommon(uid), models))
    return r, models


def local(y, m, d, hh, mm=0) -> str:
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(TZ)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def biz_settings(tmp_path) -> Settings:
    s = Settings(data_dir=tmp_path / "data", user_name="Test", llm_provider="keyword", tts_provider="console",
                 odoo_url="https://x.example.com", odoo_db="db", odoo_username="u", odoo_api_key="good",
                 business_name="Ocean View Stables", business_tz=TZ)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def today() -> date:
    return datetime.now(ZoneInfo(TZ)).date()


@pytest.fixture
def db(today):
    tomorrow = today + timedelta(days=1)
    now_utc = datetime.now(timezone.utc)
    return {
        "models": ["sale.order", "pos.order", "account.move", "crm.lead", "helpdesk.ticket"],
        "fields:calendar.event": ["appointment_type_id", "appointment_status", "total_capacity_reserved"],
        "fields:res.partner": ["sale_order_count"],
        "fields:crm.lead": ["source_id", "medium_id"],
        "fields:product.template": ["taxes_id"],
        "calendar.event": [
            {"id": 1, "name": "Chris Mosley - Horses on the Beach Experience | Guided Ride", "start": local(tomorrow.year, tomorrow.month, tomorrow.day, 9),
             "stop": "", "allday": False, "partner_ids": [11], "appointment_type_id": [5, "Horses on the Beach Experience | Guided Ride"],
             "appointment_status": "booked", "total_capacity_reserved": 2},
            {"id": 2, "name": "Nita Webb - Horses on the Beach Experience | Guided Ride", "start": local(tomorrow.year, tomorrow.month, tomorrow.day, 9),
             "stop": "", "allday": False, "partner_ids": [12], "appointment_type_id": [5, "Horses on the Beach Experience | Guided Ride"],
             "appointment_status": "cancelled", "total_capacity_reserved": 3},
            {"id": 3, "name": "Horsemanship Lessons — 30 Minutes", "start": local(tomorrow.year, tomorrow.month, tomorrow.day, 15, 30),
             "stop": "", "allday": False, "partner_ids": [13], "appointment_type_id": [7, "Horsemanship Lessons — 30 Minutes"],
             "appointment_status": "booked", "total_capacity_reserved": 0},
        ],
        "res.partner": [
            {"id": 13, "name": "Jenny Wu", "email": "jenny@example.com", "phone": "+1 415 555 0100", "city": "San Francisco",
             "create_date": "2025-03-02 18:00:00", "customer_rank": 3, "is_company": False, "parent_id": False, "sale_order_count": 3},
            {"id": 11, "name": "Chris Mosley", "email": "", "phone": "", "city": "", "create_date": "2026-09-20 01:00:00",
             "customer_rank": 1, "is_company": False, "parent_id": False, "sale_order_count": 1},
            {"id": 12, "name": "Nita Webb", "email": "", "phone": "", "city": "", "create_date": "2026-09-20 01:00:00",
             "customer_rank": 1, "is_company": False, "parent_id": False, "sale_order_count": 1},
        ],
        "sale.order": [
            {"id": 100, "name": "S02311", "partner_id": [13, "Jenny Wu"], "date_order": "2026-09-12 20:00:00", "amount_total": 240.0},
            {"id": 101, "name": "S02312", "partner_id": [11, "Chris Mosley"], "date_order": "2026-09-24 20:00:00", "amount_total": 360.0},
        ],
        "pos.order": [{"id": 5, "amount_total": 42.5}],
        "account.move": [{"id": 9, "amount_residual": 150.0}, {"id": 10, "amount_residual": 50.0}],
        "res.company": [{"id": 1, "currency_id": [1, "USD"]}],
        "crm.lead": [
            {"id": 3269, "name": "Evelyn's 12th Birthday party ride", "contact_name": "Alisson Xavier", "stage_id": [3, "Proposal Sent"],
             "create_date": (now_utc - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"), "expected_revenue": 0,
             "source_id": False, "medium_id": [2, "Website"]},
            {"id": 3270, "name": "Veteran Free Ride", "contact_name": "", "stage_id": [1, "New"],
             "create_date": (now_utc - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S"), "expected_revenue": 0,
             "source_id": False, "medium_id": False},
        ],
        "mail.message": [
            {"id": 1, "subject": "Re: Confirmed for tomorrow", "email_from": '"Sam Rider" <sam@example.com>',
             "date": (now_utc - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"), "model": "crm.lead", "res_id": 3269, "record_name": "Evelyn's 12th Birthday party ride"},
            {"id": 2, "subject": "Re: Confirmed for tomorrow", "email_from": '"Sam Rider" <sam@example.com>',
             "date": (now_utc - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"), "model": "res.partner", "res_id": 1, "record_name": "Sam Rider"},
        ],
        "helpdesk.ticket": [{"id": 1, "name": "SMS delivery failure", "stage_id": [1, "New"], "create_date": "2026-09-24 01:00:00"}],
        "mail.activity": [
            {"id": 1, "summary": "Flu / Rhino booster due for Huck", "res_model": "x_horses", "res_name": "Huck",
             "date_deadline": (today - timedelta(days=30)).isoformat(), "activity_type_id": [1, "To-Do"]},
            {"id": 2, "summary": "", "res_model": "crm.lead", "res_name": "Veteran Free Ride",
             "date_deadline": today.isoformat(), "activity_type_id": [2, "Call"]},
            {"id": 3, "summary": "Flu / Rhino booster due for Mocha", "res_model": "x_horses", "res_name": "Mocha",
             "date_deadline": (today - timedelta(days=30)).isoformat(), "activity_type_id": [1, "To-Do"]},
        ],
        "product.template": [
            {"id": 1, "name": "Horsemanship Lessons — 1 Hour", "list_price": 160.0, "taxes_id": [4]},
            {"id": 2, "name": "Private Horsemanship Lessons — 1 Hour", "list_price": 220.0, "taxes_id": [4]},
        ],
        "account.tax": [{"id": 4, "name": "Sales Tax 9.875%"}],
    }


@pytest.fixture
def ctx(biz_settings, db):
    reader, models = make_reader(db)
    business.set_reader(reader, biz_settings)
    yield SkillContext(settings=biz_settings), models
    business.set_reader(None)


# ------------------------------------------------------------------ credentials and the read-only client
def test_credentials_from_env_then_file(tmp_path):
    s = Settings(odoo_url="https://x", odoo_db="d", odoo_username="u", odoo_api_key="k")
    assert credentials_from(s)["api_key"] == "k"
    s = Settings()
    with pytest.raises(OdooNotConfigured):
        credentials_from(s)
    f = tmp_path / "creds.json"
    f.write_text(json.dumps({"url": "https://y", "db": "d2", "username": "u2", "api_key": "k2"}))
    s = Settings(odoo_credentials_path=str(f))
    creds = credentials_from(s)
    assert creds == {"url": "https://y", "db": "d2", "username": "u2", "api_key": "k2"}
    assert s.business_configured() is True
    f.write_text(json.dumps({"url": "https://y"}))
    with pytest.raises(OdooNotConfigured, match="lacks db, username, api_key"):
        credentials_from(s)
    assert Settings(odoo_credentials_path=str(tmp_path / "nope.json")).business_configured() is False


def test_reader_is_read_only_and_reports_failures(db):
    r, models = make_reader(db)
    assert r.uid == 2
    assert r.search_count("res.partner", []) == 3
    for method in ("write", "create", "unlink", "action_confirm", "execute"):
        with pytest.raises(OdooError, match="read-only"):
            r.call("res.partner", method, [1], {"name": "x"})
    assert not [c for c in models.calls if c[1] not in ("search_count",)]  # nothing reached the wire
    bad, _ = make_reader(db, key="wrong")
    with pytest.raises(OdooError, match="refused the login"):
        bad.uid
    me = r.whoami()
    assert me["server"] == "19.0+e"


def test_reader_time_helpers():
    r, _ = make_reader({})
    dt = r.to_local("2026-09-26 02:00:00")
    assert dt.hour == 19 and dt.date() == date(2026, 9, 25)  # 7 PM Pacific the evening before
    lo, hi = r.day_bounds(date(2026, 9, 26))
    assert lo == "2026-09-26 07:00:00" and hi == "2026-09-27 07:00:00"
    assert r.to_local("") is None and r.to_local("garbage") is None


# ------------------------------------------------------------------ registration
def test_business_skills_only_when_configured(settings, biz_settings):
    reg = load_default_skills(settings=settings)
    assert reg.get("bookings") is None and reg.get("price_check") is None
    reg = load_default_skills(settings=biz_settings)
    assert set(business.BUSINESS_SKILLS) <= set(reg.names())
    reg = load_default_skills(settings=settings)
    assert reg.get("bookings") is None
    own = SkillRegistry()
    business.ensure_registered(own, True)
    assert len(own) == len(business.BUSINESS_SKILLS)
    business.ensure_registered(own, False)
    assert len(own) == 0


def test_prompts_mention_the_business_only_when_configured(settings, biz_settings):
    from daxton import convai
    from daxton.brain.prompts import system_prompt

    assert "price_check" not in system_prompt(settings)
    text = system_prompt(biz_settings)
    assert "Ocean View Stables" in text and "price_check" in text and "Odoo" not in text
    reg = load_default_skills(settings=biz_settings)
    prompt = convai.agent_prompt(biz_settings, reg)
    assert "read-only" in prompt and "Ocean View Stables's system" in prompt and "Odoo" not in prompt
    assert "bookings" not in convai.agent_prompt(settings, load_default_skills(settings=settings)).split("Memory:")[1].split("After a tool")[0]


# ------------------------------------------------------------------ the skills over the fake database
def test_bookings_for_a_day(ctx, today):
    c, models = ctx
    out = business.bookings(day="tomorrow", ctx=c)
    assert out.startswith("Tomorrow,")
    assert "3 bookings, 3 riders (1 cancelled)" in out
    assert "9:00 AM Horses on the Beach Experience, Guided Ride: 2 riders: Chris Mosley x2, Nita Webb x3 (cancelled)" in out
    assert "3:30 PM Horsemanship Lessons - 30 Minutes: Jenny Wu" in out
    assert "—" not in out and "|" not in out
    domain = [c for c in models.calls if c[0] == "calendar.event" and c[1] == "search_read"][0][2][0]
    assert ["appointment_type_id", "!=", False] in domain
    assert business.bookings(day="someday", ctx=c).startswith("I don't understand the day")
    assert business.bookings(day="tomorrow", who="Zebra", ctx=c).startswith("No bookings tomorrow")


def test_bookings_for_the_week(ctx, today):
    c, models = ctx
    out = business.bookings(day="week", ctx=c)
    assert out.startswith("3 bookings, 3 riders, over 7 days:")
    assert "3 bookings, 3 riders (Horses on the Beach Experience, Guided Ride 2, Horsemanship Lessons - 30 Minutes 1)" in out


def test_find_customer(ctx):
    c, models = ctx
    out = business.find_customer(query="jenny", ctx=c)
    assert out.startswith("Jenny Wu, customer since March 2, 2025, jenny@example.com, +1 415 555 0100, San Francisco.")
    assert "this year" not in out
    assert "3 orders; last S02311 on September 12 for $240.00." in out
    assert "Next booking: tomorrow 3:30 PM, Horsemanship Lessons - 30 Minutes." in out
    assert "Other matches" not in out
    assert business.find_customer(query="nobody", ctx=c) == "No customer matches 'nobody' in Ocean View Stables's system."
    out = business.find_customer(query="415 555 0100", ctx=c)
    assert out.startswith("Jenny Wu")
    phone_domain = [call for call in models.calls if call[0] == "res.partner" and call[1] == "search_read"][-1][2][0]
    assert phone_domain == [["phone", "ilike", "0100"]]
    assert business.find_customer(query="415 555 0199", ctx=c).startswith("No customer matches")


def test_recent_leads(ctx):
    c, _ = ctx
    out = business.recent_leads(days=1, ctx=c)
    assert out.startswith("2 new leads in the last day: 1 Proposal Sent, 1 New.")
    assert "Evelyn's 12th Birthday party ride (Alisson Xavier), Proposal Sent, via Website" in out
    assert "Veteran Free Ride, New" in out


def test_sales_summary(ctx, db):
    c, models = ctx
    out = business.sales_summary(period="month", ctx=c)
    assert out.startswith("Sales this month: 2 confirmed orders for $600.00 and 1 point-of-sale orders for $42.50.")
    assert "Unpaid invoices right now: 2 for $200.00." in out
    assert business.sales_summary(period="whenever", ctx=c).startswith("I don't understand the period")
    # a server without read_group: the rows are summed instead
    db["no_read_group"] = ["sale.order"]
    out = business.sales_summary(period="last 30 days", ctx=c)
    assert "2 confirmed orders for $600.00" in out and "the last 30 days" in out


def test_inbox_and_reminders(ctx):
    c, _ = ctx
    out = business.inbox(days=1, ctx=c)
    assert out.startswith("1 incoming emails in the last day.")  # the same mail on two records is one line
    assert "Re: Confirmed for tomorrow from Sam Rider, on the lead Evelyn's 12th Birthday party ride" in out
    assert "Open support tickets: 1. Newest: SMS delivery failure (New)." in out
    out = business.reminders(ctx=c)
    assert out.startswith("3 due (2 overdue), 3 more this week.")
    assert "Flu / Rhino booster due for Huck, Mocha (horses), since" in out
    assert "Call on Veteran Free Ride (lead), today" in out


def test_price_check(ctx):
    c, models = ctx
    out = business.price_check(product="one hour lesson", ctx=c)
    assert out.startswith("Live prices: Horsemanship Lessons - 1 Hour: $160.00 list price plus Sales Tax 9.875%")
    assert "Private Horsemanship Lessons - 1 Hour: $220.00" in out
    domain = [call for call in models.calls if call[0] == "product.template" and call[1] == "search_read"][0][2][0]
    assert domain == [["sale_ok", "=", True], "|", "|", ["name", "ilike", "1"], ["name", "ilike", "hour"], ["name", "ilike", "lesson"]]
    assert business.price_check(product="zeppelin", ctx=c).startswith("No product or service matching 'zeppelin'")


def test_unconfigured_and_unreachable_answers(settings):
    business.set_reader(None)
    c = SkillContext(settings=settings)
    assert business.bookings(ctx=c).startswith("The business system is not connected:")

    class Down:
        def now(self):
            return datetime.now(ZoneInfo(TZ))

        def fields_get(self, *a, **k):
            raise OdooError("cannot reach x.example.com: timed out")

        def search_read(self, *a, **k):
            raise OdooError("cannot reach x.example.com: timed out")

        tz = ZoneInfo(TZ)

        def day_bounds(self, *a, **k):
            return "a", "b"

    business.set_reader(Down(), settings)
    try:
        assert business.bookings(ctx=c) == "The business system did not answer: cannot reach x.example.com: timed out"
    finally:
        business.set_reader(None)


def test_parse_day_and_period():
    friday = date(2026, 9, 25)
    assert business.parse_day("saturday", friday) == (date(2026, 9, 26), 1)
    assert business.parse_day("friday", friday) == (friday, 1)
    assert business.parse_day("next friday", friday) == (date(2026, 10, 2), 1)
    assert business.parse_day("weekend", friday) == (date(2026, 9, 26), 2)
    assert business.parse_day("next week", friday) == (date(2026, 9, 28), 7)
    assert business.parse_day("October 9th", friday) == (date(2026, 10, 9), 1)
    assert business.parse_day("1/5", friday) == (date(2027, 1, 5), 1)  # already past this year: next year
    assert business.parse_day("2026-10-09", friday) == (date(2026, 10, 9), 1)
    assert business.parse_day("whenever", friday) is None
    assert business._period("last month", friday)[:2] == (date(2026, 8, 1), date(2026, 9, 1))
    assert business._period("week", friday)[:2] == (date(2026, 9, 21), date(2026, 9, 28))
    assert business._period("year", friday)[2] == "this year"
    assert business._period("tomorrow", friday)[2] == "tomorrow, Saturday, September 26"


def test_secret_files_stay_out_of_the_file_tools(tmp_path, monkeypatch):
    from daxton.skills import work

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(work, "HOME", home)
    for bad in ("odoo/credentials.json", "LeydenSecrets/api_keys.json", "app/secrets.yaml", "x/token.txt", "certs/server.pem",
                "vault/80 Assets/_credentials/keys.json"):
        with pytest.raises(ValueError):
            work._safe_path(bad)
    assert work._safe_path("code/tokenizer.py").name == "tokenizer.py"
    assert work._safe_path("notes/secretary-notes.md").name == "secretary-notes.md"
