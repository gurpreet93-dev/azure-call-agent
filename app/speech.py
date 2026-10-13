"""Text to speech with Azure Speech neural voices (REST API, one request per reply, streamed).

Azure sends the MP3 as it synthesises it; synthesize_stream() passes the chunks on as they arrive, so the browser
can start playing the first words while the rest is still being made. All requests share one HTTP client, so the
TLS connection to Azure Speech is kept open and reused between replies instead of being set up for every reply.

Each language gets a native voice (a Hindi speaker for Hindi, and so on), so replies don't sound like an
English voice reading another language. If a native voice is rejected, the multilingual voice is used.
"""
import re
import time
from xml.sax.saxutils import escape, quoteattr

import httpx

from app.config import settings

OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"

# Native Azure neural voices per language in the picker. Override any with
# SPEECH_VOICES="hi-IN=hi-IN-MadhurNeural,pa-IN=pa-IN-OjasNeural".
NATIVE_VOICES = {
    "en-IN": "en-IN-NeerjaNeural",
    "hi-IN": "hi-IN-SwaraNeural",
    "bn-IN": "bn-IN-TanishaaNeural",
    "gu-IN": "gu-IN-DhwaniNeural",
    "mr-IN": "mr-IN-AarohiNeural",
    "pa-IN": "pa-IN-VaaniNeural",
    "ta-IN": "ta-IN-PallaviNeural",
    "te-IN": "te-IN-ShrutiNeural",
    "kn-IN": "kn-IN-SapnaNeural",
    "ml-IN": "ml-IN-SobhanaNeural",
    "or-IN": "or-IN-SubhasiniNeural",
    "es-ES": "es-ES-ElviraNeural",
    "fr-FR": "fr-FR-DeniseNeural",
    "de-DE": "de-DE-KatjaNeural",
    "ar-SA": "ar-SA-ZariyahNeural",
    "zh-CN": "zh-CN-XiaoxiaoNeural",
}


class SpeechError(RuntimeError):
    pass


# Unicode blocks of the scripts the agent may reply in, and the language whose voice should read them.
SCRIPTS = [
    ((0x0900, 0x097F), "hi-IN"),   # Devanagari (Hindi; Marathi if Marathi is picked)
    ((0x0980, 0x09FF), "bn-IN"),   # Bengali
    ((0x0A00, 0x0A7F), "pa-IN"),   # Gurmukhi (Punjabi)
    ((0x0A80, 0x0AFF), "gu-IN"),   # Gujarati
    ((0x0B00, 0x0B7F), "or-IN"),   # Odia
    ((0x0B80, 0x0BFF), "ta-IN"),   # Tamil
    ((0x0C00, 0x0C7F), "te-IN"),   # Telugu
    ((0x0C80, 0x0CFF), "kn-IN"),   # Kannada
    ((0x0D00, 0x0D7F), "ml-IN"),   # Malayalam
    ((0x0600, 0x06FF), "ar-SA"),   # Arabic
    ((0x4E00, 0x9FFF), "zh-CN"),   # CJK
]


def language_of(text: str, picked: str | None) -> str | None:
    """The language a reply is actually written in, judged by its script. A Latin-script reply (English,
    Spanish, ...) keeps the picked language. Devanagari is Hindi unless Marathi was picked."""
    counts: dict[str, int] = {}
    letters = 0
    for ch in text:
        if ch.isalpha():
            letters += 1
            cp = ord(ch)
            for (lo, hi), lang in SCRIPTS:
                if lo <= cp <= hi:
                    counts[lang] = counts.get(lang, 0) + 1
                    break
    if not counts or not letters:
        return picked
    lang, n = max(counts.items(), key=lambda kv: kv[1])
    if n < 0.2 * letters:          # a stray word in another script: keep the picked language
        return picked
    if lang == "hi-IN" and picked == "mr-IN":
        return "mr-IN"
    return lang


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


_client: httpx.Client | None = None


def http() -> httpx.Client:
    """One keep-alive client for all Azure Speech calls (a warm server reuses the open TLS connection)."""
    global _client
    if _client is None:
        _client = httpx.Client(timeout=httpx.Timeout(20, connect=5), headers={"User-Agent": "azure-call-agent"},
                               limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=120))
    return _client


def _request(text: str, lang: str | None, voice: str) -> httpx.Response:
    """Starts synthesis and returns as soon as Azure answers with a status; the audio body is read later."""
    url = f"https://{settings.speech_region}.tts.speech.microsoft.com/cognitiveservices/v1"
    req = http().build_request("POST", url, content=ssml(text, lang, voice).encode("utf-8"), headers={
        "Ocp-Apim-Subscription-Key": settings.speech_key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
    })
    resp = http().send(req, stream=True)
    if resp.status_code != 200:
        resp.read()          # error bodies are small; read them so the message can be shown and the connection reused
        resp.close()
    return resp


_voice_list: list[dict] | None = None
_resolved: dict[str, str] = {}   # language -> a voice Azure accepted, found from Azure's own voice list


def available_voices() -> list[dict]:
    """Azure's voice list for this region (cached): [{"ShortName", "Locale", "Gender", ...}, ...]."""
    global _voice_list
    if _voice_list is None:
        url = f"https://{settings.speech_region}.tts.speech.microsoft.com/cognitiveservices/voices/list"
        resp = http().get(url, headers={"Ocp-Apim-Subscription-Key": settings.speech_key})
        _voice_list = resp.json() if resp.status_code == 200 else []
    return _voice_list


def pick_voice(lang: str, catalogue: list[dict]) -> str | None:
    """A neural voice for exactly this language, female first (matching the other voices)."""
    matches = [v for v in catalogue if v.get("Locale") == lang and v.get("ShortName", "").endswith("Neural")]
    matches.sort(key=lambda v: v.get("Gender") != "Female")
    return matches[0]["ShortName"] if matches else None


class SpeechStream:
    """Audio being synthesised: iterate it for MP3 chunks as Azure sends them.

    timing (seconds, from the start of the call): azure_headers = Azure accepted the request,
    first_audio = first audio chunk arrived (filled in by first_chunk()). Fallback voices add to both.
    """

    def __init__(self, resp: httpx.Response, voice: str, lang: str | None, started: float):
        self.voice, self.lang, self._resp, self._started = voice, lang, resp, started
        self.timing = {"azure_headers": time.perf_counter() - started}
        self._chunks = resp.iter_bytes()
        self._first: bytes | None = None

    def first_chunk(self) -> bytes:
        """Waits for the first audio chunk (so its arrival time can be measured before the reply is sent)."""
        if self._first is None:
            self._first = next(self._chunks, b"")
            self.timing["first_audio"] = time.perf_counter() - self._started
        return self._first

    def __iter__(self):
        try:
            first = self.first_chunk()
            if first:
                yield first
            yield from self._chunks
        finally:
            self._resp.close()

    def close(self):
        self._resp.close()


def synthesize_stream(text: str, lang: str | None = None) -> SpeechStream:
    """Starts synthesis and returns the audio as a stream. If the configured voice for a language is rejected,
    a real voice for that language is looked up in Azure's voice list; the multilingual voice is the last resort."""
    if not settings.speech_key:
        raise SpeechError("Set SPEECH_KEY (or FOUNDRY_API_KEY) in .env")
    started = time.perf_counter()
    text = speakable(text)
    lang = language_of(text, lang)     # Hindi text gets a Hindi voice even if English was picked
    voice = _resolved.get(lang or "") or voice_for(lang)
    resp = _request(text, lang, voice)
    if resp.status_code == 400 and lang:
        found = pick_voice(lang, available_voices())
        if found and found != voice:
            voice = found
            resp = _request(text, lang, voice)
            if resp.status_code == 200:
                _resolved[lang] = voice
    if resp.status_code == 400 and voice != settings.speech_voice:  # still rejected: use the multilingual voice
        voice = settings.speech_voice
        resp = _request(text, lang, voice)
    if resp.status_code != 200:
        raise SpeechError(f"Azure Speech returned {resp.status_code}: {resp.text[:200] or resp.reason_phrase}")
    return SpeechStream(resp, voice, lang, started)


def synthesize_with_voice(text: str, lang: str | None = None) -> tuple[bytes, str]:
    """Returns (whole mp3, voice used): the stream, read to the end."""
    stream = synthesize_stream(text, lang)
    return b"".join(stream), stream.voice


def issue_token() -> str:
    """A 10-minute Azure Speech token for the browser's speech recognition, so the key stays on the server."""
    if not settings.speech_key:
        raise SpeechError("Set SPEECH_KEY (or FOUNDRY_API_KEY) in .env")
    url = f"https://{settings.speech_region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    resp = http().post(url, timeout=10, headers={"Ocp-Apim-Subscription-Key": settings.speech_key})
    if resp.status_code != 200:
        raise SpeechError(f"Azure Speech token request returned {resp.status_code}: {resp.text[:200]}")
    return resp.text


def synthesize(text: str, lang: str | None = None) -> bytes:
    return synthesize_with_voice(text, lang)[0]
