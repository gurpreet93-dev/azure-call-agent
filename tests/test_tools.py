from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import tools
from app.db import get_db


def _ctx(contractor):
    from app import agent
    started = agent.start_call(contractor["id"], "simulator")
    return {"contractor": contractor, "call": agent.get_call(started["call_id"]), "ended": False}


def test_slots_respect_hours_lead_time_and_busy(contractor):
    tz = ZoneInfo(contractor["timezone"])
    monday = date(2030, 1, 7)
    now = datetime(2030, 1, 7, 7, 0, tzinfo=tz)
    slots = tools.compute_slots(contractor, monday, monday, "any", now=now)
    assert slots and slots[0]["start"].startswith("2030-01-07T09:00")  # 08:00 open + 2h lead time from 07:00
    assert all(datetime.fromisoformat(s["start"]).hour < 18 for s in slots)
    sunday = date(2030, 1, 6)
    assert tools.compute_slots(contractor, sunday, sunday, "any", now=now - timedelta(days=2)) == []


def test_booking_sends_invites_to_caller_and_contractor(contractor):
    ctx = _ctx(contractor)
    day = (datetime.now(timezone.utc) + timedelta(days=3)).date()
    slots = tools.compute_slots(contractor, day, day + timedelta(days=5), "any")
    result, ok = tools.run_tool(ctx, "book_site_visit", {
        "start": slots[0]["start"], "customer_name": "Jo", "customer_email": "jo@example.com",
        "customer_phone": "+15555550123", "site_address": "1 Main St", "job_summary": "Leak over kitchen"})
    assert ok, result
    assert set(result["invites_sent_to"]) == {"jo@example.com", "owner@example.com"}
    with get_db() as db:
        invites = db.execute("SELECT to_addr FROM outbox WHERE ics IS NOT NULL").fetchall()
    assert {r["to_addr"] for r in invites} == {"jo@example.com", "owner@example.com"}
    # same slot again is now taken
    again, ok2 = tools.run_tool(ctx, "book_site_visit", {
        "start": slots[0]["start"], "customer_name": "Al", "customer_email": "al@example.com",
        "customer_phone": "1", "site_address": "2 Main St", "job_summary": "x"})
    assert not ok2 and "no longer available" in again["error"]


def test_bad_email_is_rejected_for_the_agent_to_recover(contractor):
    result, ok = tools.run_tool(_ctx(contractor), "update_caller_details", {"email": "jo at example dot com"})
    assert not ok and "spell it again" in result["error"]


def test_proposal_requires_pricing_source_and_computes_tax(contractor):
    ctx = _ctx(contractor)
    args = {"customer_name": "Jo", "customer_email": "jo@example.com", "site_address": "1 Main St",
            "scope_summary": "Replace 120 ft of 5 inch gutters",
            "line_items": [{"description": "Gutter 5 inch", "quantity": 120, "unit": "ft", "unit_price": 14}],
            "tax_rate_percent": 13, "pricing_source": " "}
    result, ok = tools.run_tool(ctx, "send_proposal", args)
    assert not ok and "pricing_source" in result["error"]
    result, ok = tools.run_tool(ctx, "send_proposal", {**args, "pricing_source": "price list row: Gutter 5 inch"})
    assert ok and result["subtotal"] == 1680 and result["total"] == 1898.4
