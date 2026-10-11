"""Outbound email. Graph (contractor's Outlook mailbox) when configured, otherwise a mock outbox
stored in SQLite and shown in the dashboard. Either way every message is recorded in `outbox`."""
import base64

from app.config import settings
from app.db import get_db, now_iso
from app.integrations import graph


def send_email(contractor: dict, call_id: int | None, to: str, subject: str, html: str,
               ics: str | None = None) -> dict:
    provider = "mock"
    if settings.graph_enabled and contractor.get("calendar_mailbox"):
        message = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html},
            "toRecipients": [{"emailAddress": {"address": to}}],
        }
        if ics:
            message["attachments"] = [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": "invite.ics",
                "contentType": "text/calendar",
                "contentBytes": base64.b64encode(ics.encode()).decode(),
            }]
        graph.request("POST", f"/users/{contractor['calendar_mailbox']}/sendMail",
                      json={"message": message, "saveToSentItems": True})
        provider = "graph"

    with get_db() as db:
        cur = db.execute(
            "INSERT INTO outbox (contractor_id, call_id, to_addr, subject, body_html, ics, provider, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (contractor["id"], call_id, to, subject, html, ics, provider, now_iso()),
        )
    return {"outbox_id": cur.lastrowid, "provider": provider, "to": to}
