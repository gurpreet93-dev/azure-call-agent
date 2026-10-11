import dataclasses
import json
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from app import agent_setup, booking, foundry_agent, gcal

IST = booking.tz()
MONDAY = datetime(2026, 10, 12, 9, 0, tzinfo=IST)        # a Monday, 9:00 India time


@pytest.fixture(autouse=True)
def practice_calendar(monkeypatch):
    monkeypatch.setattr(booking, "_practice", [])
    monkeypatch.setattr(booking, "now", lambda: MONDAY)
    monkeypatch.setattr(gcal, "configured", lambda: False)
    monkeypatch.setattr(booking, "settings", dataclasses.replace(booking.settings, contractor_email="owner@clearview.test",
                                                                business_hours="10-18", work_days="Mon-Sat",
                                                                visit_minutes=60))


def details(**over):
    d = {"name": "Asha Rao", "phone": "+91 98765 43210", "email": "asha@example.com", "address": "12 MG Road",
         "pincode": "560001", "city": "Bengaluru", "state": "Karnataka", "project_type": "home",
         "issue": "west-facing living room gets too hot"}
    d.update(over)
    return d


def test_slots_respect_hours_lead_time_and_days():
    today = booking.free_slots(MONDAY.date())
    assert today[0].hour == 11 and today[-1].hour == 17            # 2 h notice from 9:00; last visit 17:00-18:00
    assert booking.free_slots((MONDAY + timedelta(days=6)).date()) == []   # Sunday closed
    assert len(booking.free_slots((MONDAY + timedelta(days=1)).date())) == 8


def test_availability_prefers_the_callers_time_and_offers_alternatives():
    out = booking.check_availability({"preferred_date": "2026-10-13", "preferred_time": "afternoon"})
    assert [s["start"][11:16] for s in out["free_slots"]] == ["14:00", "13:00", "15:00"]
    sunday = booking.check_availability({"preferred_date": "2026-10-18", "preferred_time": ""})
    assert sunday["free_slots"] == [] and sunday["next_available"][0]["start"].startswith("2026-10-19T10:00")
    with pytest.raises(booking.ToolError):
        booking.check_availability({"preferred_date": "2026-10-01", "preferred_time": ""})


def test_booking_validates_details_then_blocks_the_slot():
    start = booking.check_availability({"preferred_date": "2026-10-13", "preferred_time": "10:00"})["free_slots"][0]["start"]
    for bad, msg in [({"email": "asha@"}, "email"), ({"phone": "12345"}, "10 digits"), ({"pincode": "5600"}, "6 digits"),
                     ({"city": ""}, "Missing: city")]:
        with pytest.raises(booking.ToolError, match=msg):
            booking.book_appointment(details(start=start, **bad))
    done = booking.book_appointment(details(start=start))
    assert done["booked"] and done["when"].startswith("Tuesday 13 October, 10:00")
    with pytest.raises(booking.ToolError, match="no longer free"):          # can't double-book
        booking.book_appointment(details(start=start, name="Someone Else"))
    assert "10:00" not in [s["start"][11:16] for s in booking.check_availability(
        {"preferred_date": "2026-10-13", "preferred_time": "10:00"})["free_slots"]]


def test_tool_errors_come_back_to_the_model_not_as_crashes():
    result, ok = booking.run_tool("book_appointment", json.dumps({"name": "x"}))
    assert not ok and "Missing" in result["error"]
    result, ok = booking.run_tool("get_current_date", "{}")
    assert ok and result["today"] == "2026-10-12" and result["weekday"] == "Monday"
    assert booking.run_tool("nope", "{}")[1] is False


def test_google_calendar_requests(monkeypatch):
    monkeypatch.setattr(gcal, "settings", dataclasses.replace(gcal.settings, google_client_id="id", google_client_secret="s",
                                                             google_refresh_token="r", google_calendar_id="primary"))
    monkeypatch.setattr(gcal, "_token", {"value": "", "expires": 0})
    sent = []

    def post(url, timeout, headers=None, json=None, data=None, params=None):
        sent.append({"url": url, "json": json, "data": data, "params": params, "headers": headers})
        if url == gcal.TOKEN_URL:
            return NS(status_code=200, json=lambda: {"access_token": "at", "expires_in": 3600})
        if url.endswith("/freeBusy"):
            return NS(status_code=200, json=lambda: {"calendars": {"primary": {"busy": [
                {"start": "2026-10-13T05:30:00Z", "end": "2026-10-13T06:30:00Z"}]}}})   # 11:00-12:00 IST
        return NS(status_code=200, json=lambda: {"id": "ev1", "htmlLink": "https://calendar.google.com/x"})

    monkeypatch.setattr(gcal.httpx, "post", post)
    monkeypatch.setattr(booking.gcal, "configured", lambda: True)
    slots = booking.free_slots(datetime(2026, 10, 13, tzinfo=IST).date())
    assert "11:00" not in [s.strftime("%H:%M") for s in slots] and "10:00" in [s.strftime("%H:%M") for s in slots]
    start = [s for s in slots if s.hour == 10][0].isoformat()
    done = booking.book_appointment(details(start=start))
    event = sent[-1]
    assert event["params"] == {"sendUpdates": "all"} and event["headers"]["Authorization"] == "Bearer at"
    assert [a["email"] for a in event["json"]["attendees"]] == ["asha@example.com", "owner@clearview.test"]
    assert event["json"]["start"]["timeZone"] == "Asia/Kolkata" and "560001" in event["json"]["location"]
    assert done["invite_sent_to"] == ["asha@example.com", "owner@clearview.test"] and done["calendar"] == "Google Calendar"
    assert sum(1 for s in sent if s["url"] == gcal.TOKEN_URL) == 1             # token reused, not refreshed every call


def test_agent_tool_loop_runs_functions_and_returns_the_spoken_answer(monkeypatch):
    rounds = []
    replies = [
        NS(output=[NS(type="function_call", name="get_current_date", arguments="{}", call_id="c1")], output_text=""),
        NS(output=[NS(type="function_call", name="check_availability", call_id="c2",
                      arguments=json.dumps({"preferred_date": "2026-10-13", "preferred_time": "10:00"}))], output_text=""),
        NS(output=[NS(type="message")], output_text="I have 10 AM, 11 AM or 9 AM free on Tuesday. Which suits you?"),
    ]

    def create(**kw):
        rounds.append(kw)
        return replies.pop(0)

    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: NS(responses=NS(create=create)))
    monkeypatch.setattr(foundry_agent, "_limits_supported", True)
    text, actions = foundry_agent.ask_with_actions("conv1", "Can someone come tomorrow morning?")
    assert text.startswith("I have 10 AM") and [a["tool"] for a in actions] == ["get_current_date", "check_availability"]
    assert rounds[0]["input"] == "Can someone come tomorrow morning?" and all(r["conversation"] == "conv1" for r in rounds)
    out = rounds[2]["input"][0]
    assert out["type"] == "function_call_output" and out["call_id"] == "c2" and "free_slots" in json.loads(out["output"])


def test_setup_creates_then_keeps_instructions(monkeypatch):
    from azure.core.exceptions import ResourceNotFoundError
    created = []
    state = {"exists": False}

    class Agents:
        def get(self, agent_name):
            if not state["exists"]:
                raise ResourceNotFoundError("nope")
            return NS(versions=NS(latest=NS(definition={"model": "gpt-4o", "instructions": "EDITED IN PORTAL",
                                                         "tools": [{"type": "file_search", "vector_store_ids": ["vs1"]},
                                                                   {"type": "function", "name": "old"}]})))

        def create_version(self, agent_name, definition, description):
            created.append(definition); state["exists"] = True
            return NS(version=str(len(created)))

    monkeypatch.setattr(agent_setup, "AIProjectClient", lambda endpoint, credential: NS(agents=Agents()))
    monkeypatch.setattr(agent_setup.foundry_agent, "credential", lambda: None)
    monkeypatch.setattr(agent_setup, "settings", dataclasses.replace(agent_setup.settings,
                        foundry_project_endpoint="https://r.services.ai.azure.com/api/projects/p",
                        foundry_agent_name="clearview-booking-agent"))
    first = agent_setup.setup_booking_agent()
    assert not first["existed"] and created[0]["instructions"] == agent_setup.STARTER_INSTRUCTIONS
    second = agent_setup.setup_booking_agent()
    tools = created[1]["tools"]
    assert second["kept_instructions"] and created[1]["instructions"] == "EDITED IN PORTAL"
    assert tools[0]["type"] == "file_search" and [t["name"] for t in tools[1:]] == [
        "get_current_date", "check_availability", "book_appointment"]
