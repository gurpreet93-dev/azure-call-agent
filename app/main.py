"""One screen: speak to the Foundry agent and hear it answer, in the language you choose."""
import base64
import binascii
import logging
import os
import re
import secrets
from pathlib import Path

import openai
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import foundry_agent, speech
from app.config import settings

log = logging.getLogger(__name__)
app = FastAPI(title="Foundry voice agent")
STATIC = Path(__file__).parent / "static"


def _password_ok(header: str | None) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        _, _, given = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return secrets.compare_digest(given.encode(), settings.app_password.encode())


@app.middleware("http")
async def require_password(request: Request, call_next):
    """With APP_PASSWORD set, every page and API call needs it (except /health for Azure's probes)."""
    if settings.app_password and request.url.path != "/health" and not _password_ok(request.headers.get("authorization")):
        return Response("Password required", status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="ClearView voice agent"'})
    return await call_next(request)


class ChatIn(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=4000)


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    lang: str | None = Field(default=None, max_length=20)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/speech-token")
def speech_token():
    """Short-lived token for Azure Speech recognition in the browser (the key never leaves the server)."""
    try:
        return {"token": speech.issue_token(), "region": settings.speech_region}
    except Exception as exc:
        log.warning("Azure Speech token failed: %s", exc)
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/conversation")
def start_conversation():
    try:
        return {"conversation_id": foundry_agent.new_conversation()}
    except Exception as exc:
        log.exception("Could not start a Foundry conversation")
        raise HTTPException(502, f"Could not reach the Foundry agent: {exc}") from exc


@app.post("/api/chat")
def chat(body: ChatIn):
    try:
        reply, actions = foundry_agent.ask_with_actions(body.conversation_id, body.text)
        return {"reply": reply, "actions": actions}
    except openai.RateLimitError as exc:
        log.warning("gpt-4o rate limit: %s", exc)
        raise HTTPException(429, "The model is over its tokens-per-minute limit. Wait about a minute and try again, "
                                 "or raise the gpt-4o deployment's rate limit in Foundry.") from exc
    except Exception as exc:
        log.exception("Foundry agent call failed")
        raise HTTPException(502, f"The Foundry agent didn't answer: {exc}") from exc


@app.post("/api/speak")
def speak(body: SpeakIn):
    try:
        audio, voice = speech.synthesize_with_voice(body.text, body.lang)
        spoken_lang = speech.language_of(speech.speakable(body.text), body.lang) or ""
        return Response(audio, media_type="audio/mpeg", headers={"X-Voice": voice, "X-Lang": spoken_lang})
    except Exception as exc:
        log.warning("Azure Speech failed, browser will fall back to its own voice: %s", exc)
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/setup-agent")
def setup_agent():
    """Create/update the booking agent in Foundry from the website (needs APP_PASSWORD set on a server)."""
    if settings.on_server and not settings.app_password:
        raise HTTPException(403, "Set APP_PASSWORD before using setup on a public site.")
    from app.agent_setup import setup_booking_agent
    try:
        return setup_booking_agent()
    except Exception as exc:
        log.exception("Agent setup failed")
        raise HTTPException(502, f"Agent setup failed: {exc}") from exc


@app.get("/setup")
def setup_page():
    return FileResponse(STATIC / "setup.html")


def _secret_fingerprint() -> dict:
    """Enough to tell which client secret this deployment holds, without revealing it. Azure's portal shows the
    first three characters of each secret's Value, so they can be compared there."""
    raw = os.environ.get("AZURE_CLIENT_SECRET", "")
    v = raw.strip()
    return {"deployed_commit": (os.environ.get("VERCEL_GIT_COMMIT_SHA") or "")[:7] or None,
            "client_id": os.environ.get("AZURE_CLIENT_ID") or None,
            "secret_set": bool(v), "secret_length": len(v), "secret_starts_with": v[:3] or None,
            "secret_has_spaces_or_newlines": raw != v or any(c.isspace() for c in v),
            "looks_like_secret_id_not_value": bool(re.fullmatch(r"[0-9a-fA-F-]{36}", v))}


@app.get("/api/calendar-status")
def calendar_status():
    from app import booking, gcal
    status = {"calendar": booking.calendar_name(), "google_configured": gcal.configured(),
              "contractor_email": settings.contractor_email or None, "timezone": settings.business_tz,
              "visit_hours": f"{settings.work_days} {settings.business_hours}"}
    status["azure_sign_in"] = _secret_fingerprint()
    if gcal.configured():
        try:
            status["slots_tomorrow"] = len(booking.free_slots(booking.now().date() + booking.timedelta(days=1)))
            status["google_ok"] = True
        except Exception as exc:
            status.update(google_ok=False, google_error=str(exc))
    return status


@app.get("/health")
def health():
    return {"ok": True, "agent": settings.foundry_agent_name or None,
            "project_endpoint_set": bool(settings.foundry_project_endpoint),
            "speech": {"region": settings.speech_region, "voice": settings.speech_voice,
                       "key_set": bool(settings.speech_key)}}
