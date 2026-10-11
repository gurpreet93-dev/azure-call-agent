"""Calendar provider. Outlook via Microsoft Graph when configured; otherwise a local mock
calendar that emails .ics invites through the mock outbox.

With Graph, creating an event with attendees makes Outlook send real meeting invites to
both the caller and the contractor - no separate email needed."""
import json
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.db import get_db, now_iso
from app.integrations import graph
from app.integrations.email import send_email


def _use_graph(contractor: dict) -> bool:
    return settings.graph_enabled and bool(contractor.get("calendar_mailbox"))


def get_busy(contractor: dict, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    if _use_graph(contractor):
        resp = graph.request(
            "GET",
            f"/users/{contractor['calendar_mailbox']}/calendarView",
            params={"startDateTime": start.astimezone(timezone.utc).isoformat(),
                    "endDateTime": end.astimezone(timezone.utc).isoformat(),
                    "$select": "start,end,showAs", "$top": "200"},
            headers={"Prefer": 'outlook.timezone="UTC"'},
        )
        busy = []
        for ev in resp.json().get("value", []):
            if ev.get("showAs") == "free":
                continue
            s = datetime.fromisoformat(ev["start"]["dateTime"][:19]).replace(tzinfo=timezone.utc)
            e = datetime.fromisoformat(ev["end"]["dateTime"][:19]).replace(tzinfo=timezone.utc)
            busy.append((s, e))
        return busy

    with get_db() as db:
        rows = db.execute(
            "SELECT start, end FROM events WHERE contractor_id=? AND end > ? AND start < ?",
            (contractor["id"], start.isoformat(), end.isoformat()),
        ).fetchall()
    return [(datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"])) for r in rows]


def build_ics(uid: str, title: str, start: datetime, end: datetime, location: str,
              description: str, organizer: str, attendees: list[str]) -> str:
    fmt = lambda dt: dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
    esc = lambda s: s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")  # noqa: E731
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Call Agent//EN", "METHOD:REQUEST",
        "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{fmt(datetime.now(timezone.utc))}",
        f"DTSTART:{fmt(start)}", f"DTEND:{fmt(end)}", f"SUMMARY:{esc(title)}",
        f"LOCATION:{esc(location)}", f"DESCRIPTION:{esc(description)}",
        f"ORGANIZER:mailto:{organizer}",
        *[f"ATTENDEE;ROLE=REQ-PARTICIPANT;RSVP=TRUE:mailto:{a}" for a in attendees],
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(lines)


def create_event(contractor: dict, call_id: int | None, title: str, start: datetime, end: datetime,
                 location: str, description: str, attendees: list[str]) -> dict:
    provider, external_id = "mock", None
    if _use_graph(contractor):
        body = {
            "subject": title,
            "body": {"contentType": "Text", "content": description},
            "start": {"dateTime": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
            "end": {"dateTime": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
            "location": {"displayName": location},
            "attendees": [{"emailAddress": {"address": a}, "type": "required"} for a in attendees],
        }
        resp = graph.request("POST", f"/users/{contractor['calendar_mailbox']}/events", json=body)
        provider, external_id = "graph", resp.json().get("id")
    else:
        uid = f"{uuid.uuid4()}@call-agent"
        ics = build_ics(uid, title, start, end, location, description, contractor["notify_email"], attendees)
        for addr in attendees:
            send_email(contractor, call_id, addr, f"Invitation: {title}",
                       f"<p>{description.replace(chr(10), '<br>')}</p>", ics=ics)
        external_id = uid

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO events (contractor_id, call_id, title, start, end, location, attendees, provider, "
            "external_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (contractor["id"], call_id, title, start.isoformat(), end.isoformat(), location,
             json.dumps(attendees), provider, external_id, now_iso()),
        )
    return {"event_id": cur.lastrowid, "provider": provider, "external_id": external_id}
