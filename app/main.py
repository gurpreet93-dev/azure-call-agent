"""One screen: speak to the Foundry agent and hear it answer, in the language you choose."""
import base64
import binascii
import logging
import secrets
import time
from pathlib import Path

import openai
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from typing import Literal

from pydantic import BaseModel, Field, model_validator

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


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    history: list[Turn] | None = Field(default=None, max_length=100)   # text-only history (the default)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=200)   # stored Foundry history
    agent: str | None = Field(default=None, max_length=100)   # one of the allowed Foundry agents; none = the default

    @model_validator(mode="after")
    def one_kind_of_history(self):
        if self.history is None and not self.conversation_id:
            raise ValueError("send either history (may be empty) or conversation_id")
        return self


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


def server_timing(**parts: float) -> str:
    """Server-Timing header (milliseconds): visible in the browser's DevTools and readable by the page."""
    return ", ".join(f"{name};dur={secs * 1000:.0f}" for name, secs in parts.items() if secs is not None)


class TrimIn(BaseModel):
    conversation_id: str = Field(pattern=r"^conv_[A-Za-z0-9_-]{1,200}$")


@app.post("/api/trim")
def trim(body: TrimIn):
    """Removes the finished turn's search results from the stored conversation (see foundry_agent)."""
    try:
        done = foundry_agent.trim_search_results(body.conversation_id)
    except Exception as exc:
        log.warning("Could not remove search results from %s: %s", body.conversation_id, exc)
        raise HTTPException(502, f"Couldn't remove the search results: {exc}") from exc
    log.info("trim removed=%d kept=%d types=%s %.0fms", done["removed"], done["kept"], done["types"],
             done["seconds"] * 1000)
    return {"removed": done["removed"], "kept": done["kept"], "types": done["types"],
            "ms": round(done["seconds"] * 1000)}


@app.post("/api/chat")
def chat(body: ChatIn):
    if body.agent and body.agent not in foundry_agent.allowed_agents():
        raise HTTPException(400, f"Unknown agent: {body.agent}")
    started = time.perf_counter()
    try:
        history = None if body.history is None else [t.model_dump() for t in body.history]
        answer = foundry_agent.ask(body.conversation_id, body.text, history, agent=body.agent)
        llm = time.perf_counter() - started
        log.info("timing llm=%.0fms chars=%d history=%s input_tokens=%s cached=%s", llm * 1000, len(answer.text),
                 "stored" if history is None else f"text:{len(history)}", answer.input_tokens, answer.cached_tokens)
        return JSONResponse({"reply": answer.text, "timing": {"llm_ms": round(llm * 1000)},
                             "tokens": {"input": answer.input_tokens, "cached": answer.cached_tokens}},
                            headers={"Server-Timing": server_timing(llm=llm)})
    except openai.APITimeoutError as exc:
        log.warning("Foundry agent timed out after %ss", foundry_agent.AGENT_TIMEOUT_S)
        raise HTTPException(504, f"The agent took longer than {foundry_agent.AGENT_TIMEOUT_S} seconds to answer. "
                                 "Try again, or ask a shorter question.") from exc
    except openai.RateLimitError as exc:
        log.warning("gpt-4o rate limit: %s", exc)
        raise HTTPException(429, "The model is over its tokens-per-minute limit. Wait about a minute and try again, "
                                 "or raise the gpt-4o deployment's rate limit in Foundry.") from exc
    except Exception as exc:
        log.exception("Foundry agent call failed")
        raise HTTPException(502, f"The Foundry agent didn't answer: {exc}") from exc


@app.post("/api/speak")
def speak(body: SpeakIn):
    """Streams the reply's audio: the first MP3 chunk goes to the browser as soon as Azure produces it."""
    try:
        stream = speech.synthesize_stream(body.text, body.lang)
        stream.first_chunk()                       # measured here, then sent first; the rest follows as it arrives
    except Exception as exc:
        log.warning("Azure Speech failed, browser will fall back to its own voice: %s", exc)
        raise HTTPException(502, str(exc)) from exc
    t = stream.timing
    log.info("timing tts azure_headers=%.0fms first_audio=%.0fms voice=%s",
             t["azure_headers"] * 1000, t["first_audio"] * 1000, stream.voice)
    return StreamingResponse(iter(stream), media_type="audio/mpeg", headers={
        "X-Voice": stream.voice, "X-Lang": stream.lang or "",
        "Server-Timing": server_timing(tts_azure_headers=t["azure_headers"], tts_first_audio=t["first_audio"]),
        "Cache-Control": "no-store"})


@app.get("/health")
def health():
    return {"ok": True, "agent": settings.foundry_agent_name or None,
            "project_endpoint_set": bool(settings.foundry_project_endpoint),
            "speech": {"region": settings.speech_region, "voice": settings.speech_voice,
                       "key_set": bool(settings.speech_key)}}
