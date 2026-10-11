"""The booking agent's tools: today's date, free site-visit slots, and booking a visit.

The Foundry agent decides when to call them (the tool definitions in TOOL_DEFINITIONS are registered on the
agent in Foundry); this module carries them out. Every tool validates its own input and returns JSON the model can
act on, including errors, so the agent can ask the caller to correct a detail and try again.

Bookings go to Google Calendar when it's configured (Google emails the invite to the caller and the contractor);
otherwise to an in-memory practice calendar, so the conversation can be tried before Google is set up.
"""
import json
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app import gcal
from app.config import settings

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
LOOKAHEAD_DAYS = 21
MIN_LEAD = timedelta(hours=2)


class ToolError(Exception):
    """Bad input: returned to the model as an error it can recover from."""


def tz() -> ZoneInfo:
    return ZoneInfo(settings.business_tz)


def now() -> datetime:
    return datetime.now(tz())


# ---------------------------------------------------------------- calendar backend

_practice: list[dict] = []      # in-memory bookings when Google isn't configured (per server instance)


def calendar_name() -> str:
    return "Google Calendar" if gcal.configured() else "practice calendar (not saved)"


def _busy(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    if gcal.configured():
        return gcal.busy_periods(start, end)
    return [(b["start"], b["end"]) for b in _practice if b["start"] < end and b["end"] > start]


def _create(summary, start, end, location, description, attendees) -> dict:
    if gcal.configured():
        ev = gcal.create_event(summary, start, end, location, description, attendees)
        return {"id": ev.get("id"), "link": ev.get("htmlLink")}
    _practice.append({"start": start, "end": end, "summary": summary})
    return {"id": f"practice-{len(_practice)}", "link": None}


# ---------------------------------------------------------------- slots

def _work_days() -> set[str]:
    raw = settings.work_days.lower().replace(" ", "")
    if "-" in raw and "," not in raw:
        a, b = raw.split("-")
        return set(WEEKDAYS[WEEKDAYS.index(a[:3]):WEEKDAYS.index(b[:3]) + 1])
    return {d[:3] for d in raw.split(",") if d}


def _hours() -> tuple[int, int]:
    a, b = settings.business_hours.split("-")
    return int(a), int(b)


def spoken(dt: datetime) -> str:
    return dt.strftime("%A %d %B, ") + dt.strftime("%I:%M %p").lstrip("0")


def free_slots(day: date, after: datetime | None = None) -> list[datetime]:
    if WEEKDAYS[day.weekday()] not in _work_days():
        return []
    open_h, close_h = _hours()
    length = timedelta(minutes=settings.visit_minutes)
    start_of_day = datetime.combine(day, time(open_h), tz())
    end_of_day = datetime.combine(day, time(close_h), tz())
    busy = _busy(start_of_day, end_of_day)
    earliest = (after or now()) + MIN_LEAD
    slots, t = [], start_of_day
    while t + length <= end_of_day:
        if t >= earliest and not any(b0 < t + length and b1 > t for b0, b1 in busy):
            slots.append(t)
        t += length
    return slots


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ToolError(f"'{value}' is not a date in YYYY-MM-DD form. Call get_current_date to work out the date.")


def _pick_near(slots: list[datetime], preferred: str | None) -> list[datetime]:
    """Up to three slots, closest to the caller's preferred time (HH:MM, 'morning', 'afternoon', 'evening')."""
    if not preferred or not slots:
        return slots[:3]
    p = preferred.strip().lower()
    target = {"morning": 10 * 60, "afternoon": 14 * 60, "evening": 17 * 60}.get(p)
    if target is None:
        m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", p)
        if not m:
            return slots[:3]
        h, mnt = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) == "pm" and h < 12:
            h += 12
        target = h * 60 + mnt
    return sorted(slots, key=lambda s: abs(s.hour * 60 + s.minute - target))[:3]


# ---------------------------------------------------------------- tools

def get_current_date(_: dict) -> dict:
    n = now()
    return {"today": n.date().isoformat(), "weekday": n.strftime("%A"), "time_now": n.strftime("%H:%M"),
            "timezone": settings.business_tz,
            "visit_hours": f"{settings.work_days}, {settings.business_hours.replace('-', ':00 to ')}:00",
            "visit_length_minutes": settings.visit_minutes}


def check_availability(args: dict) -> dict:
    day = _parse_date(args.get("preferred_date") or "")
    today = now().date()
    if day < today:
        raise ToolError("That date is in the past. Ask the caller for a future date.")
    if day > today + timedelta(days=LOOKAHEAD_DAYS):
        raise ToolError(f"Visits can be booked up to {LOOKAHEAD_DAYS} days ahead. Ask for an earlier date.")
    slots = _pick_near(free_slots(day), args.get("preferred_time"))
    if slots:
        return {"date": day.isoformat(), "free_slots": [{"start": s.isoformat(), "spoken": spoken(s)} for s in slots]}
    # nothing that day: offer the next days that have room
    alternatives, d = [], day
    while len(alternatives) < 3 and d < today + timedelta(days=LOOKAHEAD_DAYS):
        d += timedelta(days=1)
        alternatives += [{"start": s.isoformat(), "spoken": spoken(s)} for s in free_slots(d)[:1]]
    return {"date": day.isoformat(), "free_slots": [],
            "note": "No free visit slots that day.", "next_available": alternatives}


REQUIRED = ["name", "phone", "email", "address", "pincode", "city", "state", "project_type", "issue", "start"]


def book_appointment(args: dict) -> dict:
    missing = [k for k in REQUIRED if not str(args.get(k) or "").strip()]
    if missing:
        raise ToolError(f"Missing: {', '.join(missing)}. Ask the caller for these before booking.")
    a = {k: str(args[k]).strip() for k in REQUIRED}
    if not EMAIL_RE.match(a["email"]):
        raise ToolError("That email address doesn't look valid. Spell it back to the caller letter by letter.")
    digits = re.sub(r"\D", "", a["phone"])
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) != 10:
        raise ToolError("The phone number should have 10 digits. Read it back to the caller and confirm.")
    if not re.fullmatch(r"\d{6}", re.sub(r"\s", "", a["pincode"])):
        raise ToolError("The pincode should be 6 digits. Confirm it with the caller.")
    try:
        start = datetime.fromisoformat(a["start"])
    except ValueError:
        raise ToolError("'start' must be one of the slot start times returned by check_availability.")
    start = start.astimezone(tz()) if start.tzinfo else start.replace(tzinfo=tz())
    if start not in free_slots(start.date()):
        raise ToolError("That time is no longer free. Call check_availability again and offer the caller new times.")

    end = start + timedelta(minutes=settings.visit_minutes)
    location = f"{a['address']}, {a['city']}, {a['state']} {a['pincode']}"
    description = "\n".join([
        f"Site visit booked by the {settings.business_name} phone assistant.", "",
        f"Customer: {a['name']}", f"Phone: +91 {digits}", f"Email: {a['email']}",
        f"Address: {location}", f"Project type: {a['project_type']}", f"Issue / requirement: {a['issue']}"])
    attendees = [a["email"]] + ([settings.contractor_email] if settings.contractor_email else [])
    ev = _create(f"Site visit: {a['name']} ({a['project_type']})", start, end, location, description, attendees)
    return {"booked": True, "when": spoken(start), "start": start.isoformat(), "end": end.isoformat(),
            "calendar": calendar_name(), "invite_sent_to": attendees if gcal.configured() else [],
            "event_link": ev["link"], "reference": ev["id"]}


TOOLS = {"get_current_date": get_current_date, "check_availability": check_availability,
         "book_appointment": book_appointment}


def run_tool(name: str, arguments: str) -> tuple[dict, bool]:
    """Returns (result, ok). Errors are returned to the model rather than raised."""
    fn = TOOLS.get(name)
    if not fn:
        return {"error": f"Unknown tool {name}"}, False
    try:
        args = json.loads(arguments or "{}")
        return fn(args if isinstance(args, dict) else {}), True
    except ToolError as exc:
        return {"error": str(exc)}, False
    except json.JSONDecodeError:
        return {"error": "Arguments were not valid JSON."}, False
    except (gcal.CalendarError, Exception) as exc:      # calendar outages must not crash the call
        return {"error": f"The calendar is unavailable right now ({exc}). Offer to have the team call back."}, False


# JSON schemas registered on the Foundry agent (strict mode: every property required, nothing extra).
def _s(desc: str) -> dict:
    return {"type": "string", "description": desc}


TOOL_DEFINITIONS = [
    {"name": "get_current_date",
     "description": "Today's date, weekday, current time, time zone and the hours site visits can be booked. "
                    "Call it before working out any date the caller mentions (today, tomorrow, next Monday...).",
     "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
    {"name": "check_availability",
     "description": "Free site-visit slots on a date, closest to the caller's preferred time. If that day is full "
                    "it returns the next available slots. Never offer times that this tool did not return.",
     "parameters": {"type": "object", "additionalProperties": False, "required": ["preferred_date", "preferred_time"],
                    "properties": {"preferred_date": _s("Date in YYYY-MM-DD form"),
                                   "preferred_time": _s("Preferred time: HH:MM (24-hour), 'morning', 'afternoon', "
                                                        "'evening', or empty string if no preference")}}},
    {"name": "book_appointment",
     "description": "Book the site visit and send the calendar invite to the caller and the contractor. Only call "
                    "after the caller has confirmed every detail and the time, which must be a 'start' value "
                    "returned by check_availability.",
     "parameters": {"type": "object", "additionalProperties": False, "required": REQUIRED,
                    "properties": {"name": _s("Caller's full name"), "phone": _s("10-digit mobile number"),
                                   "email": _s("Email address, confirmed letter by letter"),
                                   "address": _s("Site address: house/building, street, area"),
                                   "pincode": _s("6-digit pincode"), "city": _s("City"), "state": _s("State"),
                                   "project_type": _s("Home, office, shop, facade, etc."),
                                   "issue": _s("The problem or requirement, e.g. heat, noise, safety, privacy"),
                                   "start": _s("Exact 'start' value of the chosen slot from check_availability")}}},
]
