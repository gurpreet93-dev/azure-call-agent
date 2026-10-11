"""Text to speech with Azure Speech neural voices (REST API, one request per reply)."""
from xml.sax.saxutils import escape, quoteattr

import httpx

from app.config import settings

OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"


class SpeechError(RuntimeError):
    pass


def ssml(text: str, lang: str | None, voice: str) -> str:
    """With a multilingual voice, <lang> pins the language/accent to what the user picked."""
    body = escape(text)
    if lang:
        body = f"<lang xml:lang={quoteattr(lang)}>{body}</lang>"
    return (f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>"
            f"<voice name={quoteattr(voice)}>{body}</voice></speak>")


def synthesize(text: str, lang: str | None = None) -> bytes:
    if not settings.speech_key:
        raise SpeechError("Set SPEECH_KEY (or FOUNDRY_API_KEY) in .env")
    url = f"https://{settings.speech_region}.tts.speech.microsoft.com/cognitiveservices/v1"
    resp = httpx.post(url, content=ssml(text, lang, settings.speech_voice).encode("utf-8"), timeout=20, headers={
        "Ocp-Apim-Subscription-Key": settings.speech_key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
        "User-Agent": "azure-call-agent",
    })
    if resp.status_code != 200:
        raise SpeechError(f"Azure Speech returned {resp.status_code}: {resp.text[:200] or resp.reason_phrase}")
    return resp.content
