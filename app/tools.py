"""Tools the call agent can choose between. The agent decides which (if any) to use;
nothing here is a fixed script. Each tool validates its own input and returns JSON the
model can reason over - including errors, so the agent can recover mid-call."""
import json
import re
from datetime import date, datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from app import knowledge
from app.db import get_db, now_iso
from app.integrations import calendar
from app.integrations.email import send_email

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MAX_LOOKAHEAD_DAYS = 21
MIN_LEAD_TIME = timedelta(hours=2)


class ToolError(Exception):
    """Raised for bad input; surfaced to the model as an is_error tool_result."""


TOOLS = [
    {
        "name": "search_knowledge_base",
        "description": (
            "Search the contractor's own documents (services, products, price list, warranty, "
            "service area, FAQs, past projects). Use this before answering any question about what "
            "the business offers, costs, or policies. Returns the most relevant passages with their "
            "source file. If nothing relevant comes back, say you'll have the contractor follow up "
            "rather than guessing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Keywords, e.g. 'metal roof price per square'"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_caller_details",
        "description": (
            "Save details about the caller as soon as you learn them, so nothing is lost if the call "
            "drops. Call it again whenever a detail is added or corrected. Only include fields you have."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "email": {"type": "string", "description": "Confirmed by spelling it back to the caller"},
                "site_address": {"type": "string"},
                "job_summary": {"type": "string", "description": "One or two sentences: what they need and why"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "check_availability",
        "description": (
            "Find open site-visit slots on the contractor's calendar within business hours. "
            "Call before offering times; never invent availability."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "earliest_date": {"type": "string", "description": "YYYY-MM-DD"},
                "latest_date": {"type": "string", "description": "YYYY-MM-DD, at most 21 days out"},
                "time_of_day": {"type": "string", "enum": ["any", "morning", "afternoon"]},
            },
            "required": ["earliest_date", "latest_date", "time_of_day"],
            "additionalProperties": False,
        },
    },
    {
        "name": "book_site_visit",
        "description": (
            "Book an on-site visit (inspection/measurement/consultation) in a slot returned by "
            "check_availability. Sends a calendar invite to both the caller and the contractor. "
            "Use when the job needs eyes on site before it can be quoted, or the caller asks for a visit. "
            "Confirm the time, address and email with the caller before calling this."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "Exact 'start' value from check_availability"},
                "customer_name": {"type": "string"},
                "customer_email": {"type": "string"},
                "customer_phone": {"type": "string"},
                "site_address": {"type": "string"},
                "job_summary": {"type": "string"},
            },
            "required": ["start", "customer_name", "customer_email", "customer_phone", "site_address", "job_summary"],
            "additionalProperties": False,
        },
    },
    {
        "name": "send_proposal",
        "description": (
            "Email the caller a preliminary written estimate, copied to the contractor. Use only when "
            "the job is standard enough to price from the price list without a site visit (caller gave "
            "measurements/quantities, or it is a fixed-price item) and the caller wants a quote. "
            "Every unit price MUST come from the knowledge base; cite where in pricing_source. "
            "Never invent prices; if the price list does not cover it, book a visit or escalate instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string"},
                "customer_email": {"type": "string"},
                "site_address": {"type": "string"},
                "scope_summary": {"type": "string"},
                "line_items": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit": {"type": "string"},
                            "unit_price": {"type": "number"},
                        },
                        "required": ["description", "quantity", "unit", "unit_price"],
                        "additionalProperties": False,
                    },
                },
                "tax_rate_percent": {"type": "number", "description": "From the knowledge base; 0 if not stated"},
                "pricing_source": {"type": "string", "description": "KB file(s) and lines the prices came from"},
                "assumptions": {"type": "string", "description": "What the estimate assumes / excludes"},
            },
            "required": ["customer_name", "customer_email", "site_address", "scope_summary", "line_items",
                         "tax_rate_percent", "pricing_source"],
            "additionalProperties": False,
        },
    },
    {
        "name": "escalate_to_contractor",
        "description": (
            "Alert the contractor directly. Use for: emergencies (active leak, structural damage, safety "
            "risk) -> urgency 'emergency'; an unhappy existing customer, warranty claim, or a large/commercial "
            "job outside the price list -> 'high'; a caller who just wants a message passed on -> 'normal'. "
            "Tell the caller honestly when to expect a call back."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "urgency": {"type": "string", "enum": ["emergency", "high", "normal"]},
                "reason": {"type": "string"},
                "caller_name": {"type": "string"},
                "callback_number": {"type": "string"},
                "details": {"type": "string", "description": "Everything the contractor needs to act without re-asking"},
            },
            "required": ["urgency", "reason", "callback_number", "details"],
            "additionalProperties": False,
        },
    },
    {
        "name": "end_call",
        "description": (
            "Finish the call after you've said goodbye. Records the outcome for the contractor's dashboard. "
            "Your final spoken sentence must be in the same turn as this call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome": {
                    "type": "string",
                    "enum": ["visit_booked", "proposal_sent", "escalated", "message_taken", "info_only",
                             "wrong_number_or_spam"],
                },
                "summary": {"type": "string", "description": "2-4 sentence summary for the contractor"},
            },
            "required": ["outcome", "summary"],
            "additionalProperties": False,
        },
    },
]


# ---------------------------------------------------------------- helpers

def spoken_time(dt: datetime) -> str:
    """'Wednesday October 7 at 9:30 AM'. Built by hand: strftime's %-d / %-I are Linux-only."""
    hour = dt.hour % 12 or 12
    return f"{dt:%A %B} {dt.day} at {hour}:{dt:%M %p}"


def _tz(contractor: dict) -> ZoneInfo:
    return ZoneInfo(contractor["timezone"])


def _require_email(addr: str) -> str:
    addr = addr.strip()
    if not EMAIL_RE.match(addr):
        raise ToolError(f"'{addr}' is not a valid email address. Ask the caller to spell it again.")
    return addr


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ToolError(f"Dates must be YYYY-MM-DD, got '{value}'") from exc


def _hours_for(contractor: dict, day: date) -> tuple[datetime, datetime] | None:
    hours = contractor["business_hours"].get(WEEKDAYS[day.weekday()])
    if not hours:
        return None
    tz = _tz(contractor)
    open_t, close_t = (datetime.strptime(h, "%H:%M").time() for h in hours)
    return datetime.combine(day, open_t, tz), datetime.combine(day, close_t, tz)


def _update_call(call_id: int, **fields) -> None:
    fields = {k: v for k, v in fields.items() if v}
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with get_db() as db:
        db.execute(f"UPDATE calls SET {cols} WHERE id=?", (*fields.values(), call_id))


def compute_slots(contractor: dict, earliest: date, latest: date, time_of_day: str,
                  now: datetime | None = None, limit: int = 6) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(_tz(contractor)).date()
    earliest = max(earliest, today)
    latest = min(latest, today + timedelta(days=MAX_LOOKAHEAD_DAYS))
    if latest < earliest:
        return []
    duration = timedelta(minutes=contractor["visit_duration_min"])
    range_start = datetime.combine(earliest, datetime.min.time(), _tz(contractor))
    range_end = datetime.combine(latest + timedelta(days=1), datetime.min.time(), _tz(contractor))
    busy = calendar.get_busy(contractor, range_start, range_end)

    slots, day = [], earliest
    while day <= latest and len(slots) < limit:
        window = _hours_for(contractor, day)
        per_day = 0
        if window:
            cursor, close = window
            while cursor + duration <= close and per_day < 3 and len(slots) < limit:
                end = cursor + duration
                in_part = (time_of_day == "any" or (time_of_day == "morning" and cursor.hour < 12)
                           or (time_of_day == "afternoon" and cursor.hour >= 12))
                free = all(end <= b_start or cursor >= b_end for b_start, b_end in busy)
                if in_part and free and cursor >= now + MIN_LEAD_TIME:
                    slots.append({"start": cursor.isoformat(), "label": spoken_time(cursor)})
                    per_day += 1
                    cursor = end + timedelta(minutes=30)  # spread offers out across the day
                else:
                    cursor += timedelta(minutes=30)
        day += timedelta(days=1)
    return slots


# ---------------------------------------------------------------- implementations

def search_knowledge_base(ctx: dict, query: str) -> dict:
    found = knowledge.search(ctx["contractor"]["id"], query)
    if not found["results"]:
        found["note"] = "Nothing in the knowledge base matches. Don't guess - offer a follow-up."
    return found


def update_caller_details(ctx: dict, name=None, phone=None, email=None, site_address=None, job_summary=None) -> dict:
    if email:
        email = _require_email(email)
    _update_call(ctx["call"]["id"], caller_name=name, caller_phone=phone, caller_email=email,
                 site_address=site_address, job_summary=job_summary)
    return {"saved": True}


def check_availability(ctx: dict, earliest_date: str, latest_date: str, time_of_day: str = "any") -> dict:
    slots = compute_slots(ctx["contractor"], _parse_day(earliest_date), _parse_day(latest_date), time_of_day)
    if not slots:
        return {"slots": [], "note": "No openings in that range. Offer to check a later range."}
    return {"slots": slots, "visit_length_minutes": ctx["contractor"]["visit_duration_min"]}


def book_site_visit(ctx: dict, start: str, customer_name: str, customer_email: str, customer_phone: str,
                    site_address: str, job_summary: str) -> dict:
    contractor, call = ctx["contractor"], ctx["call"]
    customer_email = _require_email(customer_email)
    try:
        start_dt = datetime.fromisoformat(start)
    except ValueError as exc:
        raise ToolError("start must be an ISO timestamp copied from check_availability") from exc
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=_tz(contractor))
    end_dt = start_dt + timedelta(minutes=contractor["visit_duration_min"])

    # Re-validate: the slot may have been taken since it was offered.
    local = start_dt.astimezone(_tz(contractor))
    valid = compute_slots(contractor, local.date(), local.date(), "any", limit=48)
    window = _hours_for(contractor, local.date())
    within_hours = window and window[0] <= local and end_dt <= window[1]
    busy = calendar.get_busy(contractor, start_dt, end_dt)
    if not within_hours or busy or start_dt < datetime.now(timezone.utc) + MIN_LEAD_TIME:
        alternatives = [s for s in valid][:3]
        raise ToolError(f"That time is no longer available. Nearby open slots: {json.dumps(alternatives)}")

    title = f"Site visit: {customer_name} - {contractor['business_name']}"
    description = (
        f"Site visit booked by phone assistant.\n\nCustomer: {customer_name}\nPhone: {customer_phone}\n"
        f"Email: {customer_email}\nAddress: {site_address}\n\nJob: {job_summary}"
    )
    event = calendar.create_event(contractor, call["id"], title, start_dt, end_dt, site_address, description,
                                  attendees=[customer_email, contractor["notify_email"]])
    _update_call(call["id"], caller_name=customer_name, caller_email=customer_email,
                 caller_phone=customer_phone, site_address=site_address, job_summary=job_summary)
    return {
        "booked": True,
        "when": spoken_time(local),
        "invites_sent_to": [customer_email, contractor["notify_email"]],
        "calendar": event["provider"],
    }


def send_proposal(ctx: dict, customer_name: str, customer_email: str, site_address: str, scope_summary: str,
                  line_items: list[dict], tax_rate_percent: float, pricing_source: str,
                  assumptions: str = "") -> dict:
    contractor, call = ctx["contractor"], ctx["call"]
    customer_email = _require_email(customer_email)
    if not pricing_source.strip():
        raise ToolError("pricing_source is required: cite the knowledge-base document the prices came from.")
    for item in line_items:
        if item["quantity"] <= 0 or item["unit_price"] < 0:
            raise ToolError(f"Invalid quantity/price on line item: {item}")

    subtotal = round(sum(i["quantity"] * i["unit_price"] for i in line_items), 2)
    tax = round(subtotal * tax_rate_percent / 100, 2)
    total = round(subtotal + tax, 2)

    rows = "".join(
        f"<tr><td>{escape(i['description'])}</td><td align=right>{i['quantity']:g} {escape(i['unit'])}</td>"
        f"<td align=right>${i['unit_price']:,.2f}</td><td align=right>${i['quantity'] * i['unit_price']:,.2f}</td></tr>"
        for i in line_items
    )
    html = f"""
<div style="font-family:system-ui,sans-serif;max-width:640px">
  <h2>{escape(contractor['business_name'])} - Preliminary Estimate</h2>
  <p>Hi {escape(customer_name)},</p>
  <p>Thanks for calling. Based on what you described, here is a preliminary estimate for
     <b>{escape(site_address)}</b>.</p>
  <p><b>Scope:</b> {escape(scope_summary)}</p>
  <table cellpadding=6 style="border-collapse:collapse;width:100%" border=1>
    <tr><th align=left>Item</th><th>Qty</th><th>Unit price</th><th>Amount</th></tr>
    {rows}
    <tr><td colspan=3 align=right>Subtotal</td><td align=right>${subtotal:,.2f}</td></tr>
    <tr><td colspan=3 align=right>Tax ({tax_rate_percent:g}%)</td><td align=right>${tax:,.2f}</td></tr>
    <tr><td colspan=3 align=right><b>Total</b></td><td align=right><b>${total:,.2f}</b></td></tr>
  </table>
  <p><b>Assumptions:</b> {escape(assumptions or 'Measurements as provided by the customer.')}</p>
  <p style="color:#666">This is a preliminary estimate prepared from our standard price list and the details
     you gave over the phone. Final pricing is confirmed after a site visit. Valid for 30 days.</p>
  <p>{escape(contractor['owner_name'])}<br>{escape(contractor['business_name'])}</p>
</div>"""
    subject = f"Your estimate from {contractor['business_name']}"
    send_email(contractor, call["id"], customer_email, subject, html)
    send_email(contractor, call["id"], contractor["notify_email"], f"[Copy] {subject} - {customer_name}",
               html + f"<hr><p><small>Pricing source cited by assistant: {escape(pricing_source)}</small></p>")
    _update_call(call["id"], caller_name=customer_name, caller_email=customer_email, site_address=site_address,
                 job_summary=scope_summary)
    return {"sent": True, "to": customer_email, "subtotal": subtotal, "tax": tax, "total": total}


def escalate_to_contractor(ctx: dict, urgency: str, reason: str, callback_number: str, details: str,
                           caller_name: str = "") -> dict:
    contractor, call = ctx["contractor"], ctx["call"]
    prefix = {"emergency": "URGENT", "high": "Priority", "normal": "Message"}[urgency]
    html = (
        f"<h3>{prefix}: {escape(reason)}</h3>"
        f"<p><b>Caller:</b> {escape(caller_name or 'unknown')}<br><b>Call back:</b> {escape(callback_number)}</p>"
        f"<p>{escape(details)}</p>"
    )
    send_email(contractor, call["id"], contractor["notify_email"], f"[{prefix}] {reason}", html)
    # Production: also text contractor['notify_phone'] via Twilio Messages API for emergencies.
    _update_call(call["id"], caller_name=caller_name, caller_phone=callback_number)
    # Deliberately no specific times: the KB forbids promising immediate attendance.
    expectation = {"emergency": "the team has been alerted as urgent and will call back as soon as possible",
                   "high": "a specialist will call back, usually the same business day",
                   "normal": "the message has been passed on for a call back"}[urgency]
    return {"escalated": True, "tell_caller_callback_expected": expectation}


def end_call(ctx: dict, outcome: str, summary: str) -> dict:
    with get_db() as db:
        db.execute("UPDATE calls SET status='completed', outcome=?, summary=?, ended_at=? WHERE id=?",
                   (outcome, summary, now_iso(), ctx["call"]["id"]))
    ctx["ended"] = True
    return {"ended": True}


HANDLERS = {
    "search_knowledge_base": search_knowledge_base,
    "update_caller_details": update_caller_details,
    "check_availability": check_availability,
    "book_site_visit": book_site_visit,
    "send_proposal": send_proposal,
    "escalate_to_contractor": escalate_to_contractor,
    "end_call": end_call,
}


def run_tool(ctx: dict, name: str, args: dict) -> tuple[dict, bool]:
    """Execute a tool, log it as an action, and return (result, ok)."""
    handler = HANDLERS.get(name)
    try:
        if handler is None:
            raise ToolError(f"Unknown tool {name}")
        result, ok = handler(ctx, **args), True
    except ToolError as exc:
        result, ok = {"error": str(exc)}, False
    except TypeError as exc:  # missing / unexpected arguments
        result, ok = {"error": f"Bad arguments: {exc}"}, False
    except Exception as exc:  # integration failure - let the agent apologise and fall back
        result, ok = {"error": f"{type(exc).__name__}: {exc}. Fall back to taking a message."}, False
    with get_db() as db:
        db.execute(
            "INSERT INTO actions (call_id, contractor_id, type, payload, result, ok, created_at) VALUES (?,?,?,?,?,?,?)",
            (ctx["call"]["id"], ctx["contractor"]["id"], name, json.dumps(args), json.dumps(result), int(ok), now_iso()),
        )
    return result, ok
