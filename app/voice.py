"""Twilio phone channel (decision log D1, option B).

Twilio owns the phone number and does speech-to-text (<Gather input="speech">) and
text-to-speech (<Say>). We only exchange text with it via TwiML webhooks:

  call comes in -> POST /voice/incoming -> greeting + <Gather>
  caller speaks -> POST /voice/turn (SpeechResult) -> agent.handle_turn -> <Say> + <Gather>
  agent calls end_call -> <Say> goodbye + <Hangup/>
  caller hangs up -> POST /voice/status -> call marked completed
"""
import base64
import hashlib
import hmac
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from app import agent
from app.accounts import contractor_for_number
from app.config import settings

router = APIRouter(prefix="/voice")
VOICE = "Polly.Joanna-Neural"


def _twiml(body: str) -> Response:
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>', media_type="application/xml")


def _say_and_listen(text: str) -> Response:
    # actionOnEmptyResult: still POST to /voice/turn on silence so we can re-prompt.
    return _twiml(
        f'<Gather input="speech" action="/voice/turn" method="POST" speechTimeout="auto" '
        f'language="en-US" actionOnEmptyResult="true">'
        f'<Say voice="{VOICE}">{escape(text)}</Say></Gather>'
    )


async def _verified_form(request: Request) -> dict:
    """Validate X-Twilio-Signature (HMAC-SHA1 of URL + sorted params) when a token is configured."""
    form = dict(await request.form())
    if settings.twilio_auth_token:
        url = settings.public_base_url.rstrip("/") + request.url.path
        payload = url + "".join(f"{k}{form[k]}" for k in sorted(form))
        expected = base64.b64encode(
            hmac.new(settings.twilio_auth_token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
        if not hmac.compare_digest(expected, request.headers.get("X-Twilio-Signature", "")):
            raise HTTPException(403, "Bad Twilio signature")
    return form


@router.post("/incoming")
async def incoming(request: Request):
    form = await _verified_form(request)
    contractor = contractor_for_number(form.get("To"))
    if contractor is None:
        return _twiml(f'<Say voice="{VOICE}">Sorry, this number is not configured yet. Goodbye.</Say><Hangup/>')
    started = agent.start_call(contractor["id"], "phone", caller_number=form.get("From"),
                               external_id=form["CallSid"])
    return _say_and_listen(started["say"])


@router.post("/turn")
async def turn(request: Request):
    form = await _verified_form(request)
    speech = (form.get("SpeechResult") or "").strip()
    if not speech:
        return _say_and_listen("Sorry, I didn't catch that. Could you say it again?")
    result = await run_in_threadpool(agent.handle_turn, form["CallSid"], speech)  # blocking LLM calls
    if result["ended"]:
        return _twiml(f'<Say voice="{VOICE}">{escape(result["say"])}</Say><Hangup/>')
    return _say_and_listen(result["say"])


@router.post("/status")
async def status(request: Request):
    form = await _verified_form(request)
    if form.get("CallStatus") in {"completed", "busy", "failed", "no-answer", "canceled"}:
        agent.hang_up(form["CallSid"])
    return Response(status_code=204)
