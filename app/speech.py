"""Text to speech with Azure Speech neural voices (REST API, one request per reply).

Each language gets a native voice (a Hindi speaker for Hindi, and so on), so replies don't sound like an
English voice reading another language. If a native voice is rejected, the multilingual voice is used.
"""
import re
from xml.sax.saxutils import escape, quoteattr

import httpx

from app.config import settings

OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"

# Native Azure neural voices per language in the picker. Override any with
# SPEECH_VOICES="hi-IN=hi-IN-MadhurNeural,pa-IN=pa-IN-OjasNeural".
NATIVE_VOICES = {
    "en-IN": "en-IN-NeerjaNeural",
    "hi-IN": "hi-IN-SwaraNeural",
    "pa-IN": "pa-IN-VaaniNeural",
    "es-ES": "es-ES-ElviraNeural",
    "fr-FR": "fr-FR-DeniseNeural",
    "de-DE": "de-DE-KatjaNeural",
    "ar-SA": "ar-SA-ZariyahNeural",
    "zh-CN": "zh-CN-XiaoxiaoNeural",
}


class SpeechError(RuntimeError):
    pass


def speakable(text: str) -> str:
    """Strip chat formatting (markdown bold, headings, list markers, links) so the voice doesn't read it out."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)          # [label](url) -> label
    text = re.sub(r"(\*\*|__|\*|`|#+ )", "", text)                    # bold, italics, code, headings
    text = re.sub(r"(?m)^\s*(?:[-•]|\d+[.)])\s+", "", text)          # list markers at line start
    text = re.sub(r"【[^】]*】", "", text)                               # file-search citation marks
    return re.sub(r"\s*\n+\s*", " ", text).strip()


def voices() -> dict[str, str]:
    table = dict(NATIVE_VOICES)
    for pair in settings.speech_voices.split(","):
        lang, _, voice = pair.partition("=")
        if lang.strip() and voice.strip():
            table[lang.strip()] = voice.strip()
    return table


def voice_for(lang: str | None) -> str:
    return voices().get(lang or "", settings.speech_voice)


def ssml(text: str, lang: str | None, voice: str) -> str:
    """A native voice already speaks its language; a multilingual voice gets <lang> to pin the language."""
    body = escape(text)
    multilingual = "Multilingual" in voice
    if lang and multilingual:
        body = f"<lang xml:lang={quoteattr(lang)}>{body}</lang>"
    doc_lang = lang if (lang and not multilingual) else "en-US"
    return (f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang={quoteattr(doc_lang)}>"
            f"<voice name={quoteattr(voice)}>{body}</voice></speak>")


def _request(text: str, lang: str | None, voice: str) -> httpx.Response:
    url = f"https://{settings.speech_region}.tts.speech.microsoft.com/cognitiveservices/v1"
    return httpx.post(url, content=ssml(text, lang, voice).encode("utf-8"), timeout=20, headers={
        "Ocp-Apim-Subscription-Key": settings.speech_key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
        "User-Agent": "azure-call-agent",
    })


def synthesize_with_voice(text: str, lang: str | None = None) -> tuple[bytes, str]:
    """Returns (mp3 audio, voice used)."""
    if not settings.speech_key:
        raise SpeechError("Set SPEECH_KEY (or FOUNDRY_API_KEY) in .env")
    text = speakable(text)
    voice = voice_for(lang)
    resp = _request(text, lang, voice)
    if resp.status_code == 400 and voice != settings.speech_voice:  # voice not available: use the multilingual one
        voice = settings.speech_voice
        resp = _request(text, lang, voice)
    if resp.status_code != 200:
        raise SpeechError(f"Azure Speech returned {resp.status_code}: {resp.text[:200] or resp.reason_phrase}")
    return resp.content, voice


def synthesize(text: str, lang: str | None = None) -> bytes:
    return synthesize_with_voice(text, lang)[0]
