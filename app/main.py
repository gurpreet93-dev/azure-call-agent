"""One screen: speak to the Foundry agent and hear it answer, in the language you choose."""
import base64
import binascii
import logging
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
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


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


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
        return {"reply": foundry_agent.ask(body.conversation_id, body.text)}
    except Exception as exc:
        log.exception("Foundry agent call failed")
        raise HTTPException(502, f"The Foundry agent didn't answer: {exc}") from exc


@app.post("/api/speak")
def speak(body: SpeakIn):
    try:
        audio, voice = speech.synthesize_with_voice(body.text, body.lang)
        return Response(audio, media_type="audio/mpeg", headers={"X-Voice": voice})
    except Exception as exc:
        log.warning("Azure Speech failed, browser will fall back to its own voice: %s", exc)
        raise HTTPException(502, str(exc)) from exc


@app.get("/health")
def health():
    return {"ok": True, "agent": settings.foundry_agent_name or None,
            "project_endpoint_set": bool(settings.foundry_project_endpoint),
            "speech": {"region": settings.speech_region, "voice": settings.speech_voice,
                       "key_set": bool(settings.speech_key)}}
