"""Google Calendar for the booking agent: free/busy lookups and creating events with invites.

Uses your own Google account through OAuth (a refresh token you grant once), because Google only lets service
accounts invite attendees on company Workspace domains, not on a personal Gmail calendar. With sendUpdates=all,
Google emails the invitation to every attendee (the caller and the contractor).
"""
import time
from datetime import datetime

import httpx

from app.config import settings

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"


class CalendarError(RuntimeError):
    pass


def configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret and settings.google_refresh_token)


_token: dict = {"value": "", "expires": 0.0}


def _access_token() -> str:
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    resp = httpx.post(TOKEN_URL, timeout=15, data={
        "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
        "refresh_token": settings.google_refresh_token, "grant_type": "refresh_token"})
    if resp.status_code != 200:
        raise CalendarError(f"Google sign-in failed ({resp.status_code}): {resp.text[:200]}")
    body = resp.json()
    _token.update(value=body["access_token"], expires=time.time() + int(body.get("expires_in", 3600)))
    return _token["value"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {_access_token()}"}


def busy_periods(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    resp = httpx.post(f"{API}/freeBusy", timeout=15, headers=_headers(), json={
        "timeMin": start.isoformat(), "timeMax": end.isoformat(), "timeZone": settings.business_tz,
        "items": [{"id": settings.google_calendar_id}]})
    if resp.status_code != 200:
        raise CalendarError(f"Google free/busy failed ({resp.status_code}): {resp.text[:200]}")
    cal = resp.json().get("calendars", {}).get(settings.google_calendar_id, {})
    if cal.get("errors"):
        raise CalendarError(f"Google calendar error: {cal['errors']}")
    return [(datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
             datetime.fromisoformat(b["end"].replace("Z", "+00:00"))) for b in cal.get("busy", [])]


def create_event(summary: str, start: datetime, end: datetime, location: str, description: str,
                 attendees: list[str]) -> dict:
    resp = httpx.post(f"{API}/calendars/{settings.google_calendar_id}/events", timeout=20, headers=_headers(),
                      params={"sendUpdates": "all"}, json={
                          "summary": summary, "location": location, "description": description,
                          "start": {"dateTime": start.isoformat(), "timeZone": settings.business_tz},
                          "end": {"dateTime": end.isoformat(), "timeZone": settings.business_tz},
                          "attendees": [{"email": a} for a in attendees],
                          "reminders": {"useDefault": True}})
    if resp.status_code not in (200, 201):
        raise CalendarError(f"Google could not create the event ({resp.status_code}): {resp.text[:200]}")
    return resp.json()
