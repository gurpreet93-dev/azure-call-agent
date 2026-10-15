"""Lead capture: when a call ends, pull the caller's details out of the conversation and add a row to a Google Sheet.

The agent collects the details by talking (its prompt lists them); this module only reads the finished call.
A model turns the transcript into fields, code decides whether there is a lead at all and what is missing, and a
Google Apps Script web app bound to the sheet writes the row (one row per call, updated if the call is saved again).
"""
import json
import logging
import re
import time

import httpx

from app import foundry_agent
from app.config import settings

log = logging.getLogger(__name__)

# Fields in the order the agent's prompt asks for them. Details 1 to 8 make a complete lead.
FIELDS = ["name", "mobile", "email", "address", "city", "district", "pincode", "requirement", "product"]
REQUIRED = FIELDS[:8]
HEADERS = ["Saved at", "Conversation ID", "Name", "Mobile", "Email", "Address", "City", "District", "Pincode",
           "Requirement", "Product", "Language", "Complete", "Missing", "Summary"]

EXTRACT = (
    "You read the transcript of a phone call between a caller and Natasha, the assistant of a glass installer, "
    "and return the caller's lead details as JSON. Use only what the caller said and confirmed in this call; if "
    "they corrected a detail, use the corrected value. Leave a field empty when it wasn't given; never guess. "
    "mobile: digits only, keep a leading +91 if said. email: lower case. requirement: what they need and for which "
    "space, in a few words. product: a specific product the caller named, else empty. language: the language the "
    "caller mostly spoke. summary: one sentence on what the caller wanted, in English.")
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {f: {"type": "string"} for f in FIELDS + ["language", "summary"]},
    "required": FIELDS + ["language", "summary"],
}
CITATION = re.compile(r"【[^】]*】")


class LeadSheetNotConfigured(RuntimeError):
    pass


def _text(item) -> str:
    parts = getattr(item, "content", None) or []
    return " ".join(getattr(p, "text", "") or "" for p in parts if getattr(p, "text", None)).strip()


def transcript(conversation_id: str) -> list[dict]:
    """The call's spoken turns from Foundry's stored conversation, oldest first (search items and notes left out)."""
    client = foundry_agent._openai_client()
    turns, after = [], None
    for _ in range(10):                                   # 100 items a page; a call is far shorter than 1,000
        page = client.conversations.items.list(conversation_id, limit=100, order="asc",
                                               **({"after": after} if after else {}))
        for item in page.data:
            role = getattr(item, "role", None)
            if getattr(item, "type", "") == "message" and role in ("user", "assistant"):
                text = CITATION.sub("", _text(item)).strip()
                if text:
                    turns.append({"role": role, "content": text})
        if not getattr(page, "has_more", False) or not page.data:
            break
        after = page.data[-1].id
    return turns


def extract(turns: list[dict]) -> dict:
    lines = "\n".join(("Caller: " if t["role"] == "user" else "Natasha: ") + t["content"] for t in turns)
    r = foundry_agent._openai_client().responses.create(
        model=settings.lead_model, input=[{"role": "system", "content": EXTRACT}, {"role": "user", "content": lines}],
        text={"format": {"type": "json_schema", "name": "lead", "schema": SCHEMA, "strict": True}},
        max_output_tokens=500)
    data = json.loads(r.output_text)
    return {k: str(data.get(k, "")).strip() for k in FIELDS + ["language", "summary"]}


def row(lead: dict, conversation_id: str) -> dict:
    missing = [f for f in REQUIRED if not lead[f]]
    values = {
        "Saved at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 5.5 * 3600)) + " IST",
        "Conversation ID": conversation_id,
        **{h: lead[f] for h, f in zip(HEADERS[2:11], FIELDS)},
        "Language": lead["language"], "Complete": "yes" if not missing else "no",
        "Missing": ", ".join(missing), "Summary": lead["summary"],
    }
    for h in ("Mobile", "Pincode"):                       # keep leading zeros and stop the sheet reading a number
        if values[h]:
            values[h] = "'" + values[h]
    return values


def post(values: dict) -> bool:
    """Writes the row through the sheet's Apps Script. Returns True when an earlier row of this call was updated."""
    if not settings.lead_sheet_url:
        raise LeadSheetNotConfigured("No Google Sheet is connected (LEAD_SHEET_URL).")
    # Apps Script answers a POST with a redirect to the result, which must be fetched with GET; httpx does that.
    r = httpx.post(settings.lead_sheet_url, json={"token": settings.lead_sheet_token, "row": values},
                   follow_redirects=True, timeout=20)
    r.raise_for_status()
    try:
        result = r.json()
    except ValueError:
        raise RuntimeError("The sheet's script didn't answer with JSON. Is it deployed as a web app?") from None
    if not result.get("ok"):
        raise RuntimeError("The sheet refused the row: " + str(result.get("error", "unknown error")))
    return bool(result.get("updated"))


def save(conversation_id: str | None = None, turns: list[dict] | None = None) -> dict:
    """Reads the call, and adds or updates its row when the caller gave at least a name or a mobile number."""
    started = time.monotonic()
    if not settings.lead_sheet_url:                       # check before spending a model call on the transcript
        raise LeadSheetNotConfigured("No Google Sheet is connected (LEAD_SHEET_URL).")
    turns = turns if turns is not None else transcript(conversation_id)
    if not any(t["role"] == "user" for t in turns):
        return {"saved": False, "reason": "nothing was said in this call"}
    lead = extract(turns)
    if not (lead["name"] or lead["mobile"]):
        return {"saved": False, "reason": "no lead details in this call (no name or mobile number)"}
    values = row(lead, conversation_id or "")
    updated = post(values)
    log.info("lead saved conversation=%s complete=%s missing=%s", conversation_id, values["Complete"],
             values["Missing"])
    return {"saved": True, "updated": updated, "lead": lead, "complete": values["Complete"] == "yes",
            "missing": [f for f in REQUIRED if not lead[f]], "ms": round((time.monotonic() - started) * 1000)}
