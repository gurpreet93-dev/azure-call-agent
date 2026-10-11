"""Minimal Microsoft Graph client (app-only / client-credentials flow).

Required application permissions on the Entra app registration:
  Calendars.ReadWrite  - read free/busy and create events (Outlook sends the invites)
  Mail.Send            - send proposal / confirmation emails from the contractor mailbox
"""
import time

import httpx

from app.config import settings

GRAPH = "https://graph.microsoft.com/v1.0"
_token: dict = {"value": None, "expires": 0.0}


def _access_token() -> str:
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    resp = httpx.post(
        f"https://login.microsoftonline.com/{settings.ms_tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": settings.ms_client_id,
            "client_secret": settings.ms_client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=15,
    )
    resp.raise_for_status()
    body = resp.json()
    _token["value"] = body["access_token"]
    _token["expires"] = time.time() + body.get("expires_in", 3600)
    return _token["value"]


def request(method: str, path: str, **kwargs) -> httpx.Response:
    headers = {"Authorization": f"Bearer {_access_token()}", **kwargs.pop("headers", {})}
    resp = httpx.request(method, f"{GRAPH}{path}", headers=headers, timeout=20, **kwargs)
    resp.raise_for_status()
    return resp
