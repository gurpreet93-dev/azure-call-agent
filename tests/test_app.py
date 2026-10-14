import dataclasses
from types import SimpleNamespace as NS

import pytest
import httpx
from fastapi.testclient import TestClient

from app import foundry_agent, main, speech


@pytest.fixture
def client():
    return TestClient(main.app)


def fake_stream(audio=b"ID3fake-mp3", voice="hi-IN-SwaraNeural"):
    """What speech.synthesize_stream returns, for a reply whose audio is `audio`."""
    def make(text, lang=None):
        lang = speech.language_of(speech.speakable(text), lang)
        return speech.SpeechStream(httpx.Response(200, content=audio), voice, lang, 0.0)
    return make


@pytest.fixture
def azure_tts(monkeypatch):
    """Routes speech.http() to a handler(request) -> httpx.Response instead of Azure."""
    def install(handler):
        monkeypatch.setattr(speech, "settings", dataclasses.replace(speech.settings, speech_key="k",
                                                                    speech_region="eastus2"))
        monkeypatch.setattr(speech, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    yield install
    speech._client = None


def test_page_has_mic_language_picker_and_text_box(client):
    html = client.get("/").text
    for needle in ('id="mic"', 'id="lang"', 'value="hi-IN"', 'id="typed"', "/api/speak"):
        assert needle in html


def test_conversation_chat_and_speak_go_to_foundry_and_speech(client, monkeypatch):
    calls = []
    monkeypatch.setattr(foundry_agent, "new_conversation", lambda: "conv_123")
    monkeypatch.setattr(foundry_agent, "ask", lambda cid, text, history=None, agent=None: calls.append((cid, text)) or foundry_agent.Answer("Namaste! Kaise madad karun?"))
    monkeypatch.setattr(speech, "synthesize_stream", fake_stream())

    cid = client.post("/api/conversation").json()["conversation_id"]
    reply = client.post("/api/chat", json={"conversation_id": cid, "text": "Mera chhat tapak raha hai"}).json()["reply"]
    audio = client.post("/api/speak", json={"text": reply, "lang": "hi-IN"})

    assert cid == "conv_123" and calls == [("conv_123", "Mera chhat tapak raha hai")]
    assert reply.startswith("Namaste")
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg" and audio.content == b"ID3fake-mp3"
    assert audio.headers["x-voice"] == "hi-IN-SwaraNeural"


def test_failures_return_readable_errors(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("PermissionDenied")
    monkeypatch.setattr(foundry_agent, "ask", boom)
    monkeypatch.setattr(speech, "synthesize_stream", boom)
    r = client.post("/api/chat", json={"conversation_id": "c", "text": "hi"})
    assert r.status_code == 502 and "PermissionDenied" in r.json()["detail"]
    assert client.post("/api/speak", json={"text": "hi"}).status_code == 502  # browser then uses its own voice
    assert client.post("/api/chat", json={"conversation_id": "c", "text": ""}).status_code == 422


def test_agent_is_called_by_name_with_server_side_conversation(monkeypatch):
    sent = {}

    class FakeOpenAI:
        conversations = NS(create=lambda: NS(id="conv_9"))
        responses = NS(create=lambda **kw: sent.update(kw) or NS(output_text="  Hello there.  "))

    monkeypatch.setattr(foundry_agent, "settings", dataclasses.replace(
        foundry_agent.settings, foundry_project_endpoint="https://r.services.ai.azure.com/api/projects/p",
        foundry_agent_name="clearview-glass-agent"))
    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: FakeOpenAI())
    assert foundry_agent.new_conversation() == "conv_9"
    assert foundry_agent.ask("conv_9", "hi").text == "Hello there."
    assert sent == {"conversation": "conv_9",
                    "input": [{"type": "message", "role": "system", "content": foundry_agent.SEARCH_REMINDER},
                              {"type": "message", "role": "user", "content": "hi"}],
                    "extra_body": {"agent_reference": {"name": "clearview-glass-agent", "type": "agent_reference"}},
                    "max_output_tokens": 400, "max_tool_calls": 2, "truncation": "auto"}


def test_missing_settings_are_reported(monkeypatch):
    foundry_agent._openai_client.cache_clear()
    monkeypatch.setattr(foundry_agent, "settings", dataclasses.replace(foundry_agent.settings,
                                                                       foundry_project_endpoint="", foundry_agent_name=""))
    with pytest.raises(foundry_agent.AgentNotConfigured):
        foundry_agent.new_conversation()
    foundry_agent._openai_client.cache_clear()


def test_ssml_escapes_text_and_pins_language():
    xml = speech.ssml('Price is <$500> & "fair"', "hi-IN", "en-US-AvaMultilingualNeural")
    assert "&lt;$500&gt; &amp;" in xml and "<lang xml:lang=\"hi-IN\">" in xml
    assert "<voice name=\"en-US-AvaMultilingualNeural\">" in xml
    assert "<lang" not in speech.ssml("hi", None, "v")
    native = speech.ssml("नमस्ते", "hi-IN", "hi-IN-SwaraNeural")   # native voice: no <lang>, document in Hindi
    assert "<lang" not in native and "xml:lang=\"hi-IN\"" in native


def test_each_language_gets_a_native_voice_with_overrides(monkeypatch):
    assert speech.voice_for("hi-IN") == "hi-IN-SwaraNeural"
    assert speech.voice_for("en-US") == speech.settings.speech_voice      # not in the table: default voice
    monkeypatch.setattr(speech, "settings", dataclasses.replace(speech.settings,
                                                                speech_voices="hi-IN=hi-IN-MadhurNeural, xx=  "))
    assert speech.voice_for("hi-IN") == "hi-IN-MadhurNeural"


def test_rejected_native_voice_falls_back_to_multilingual(monkeypatch, azure_tts):
    tried = []

    def handler(request):
        voice = "hi-IN-SwaraNeural" if "SwaraNeural" in request.content.decode() else "multi"
        tried.append(voice)
        return httpx.Response(400 if voice == "hi-IN-SwaraNeural" else 200,
                              content=b"bad voice" if voice == "hi-IN-SwaraNeural" else b"mp3")

    azure_tts(handler)
    monkeypatch.setattr(speech, "available_voices", lambda: [])   # no Hindi voice found in Azure's list either
    monkeypatch.setattr(speech, "_resolved", {})
    audio, voice = speech.synthesize_with_voice("नमस्ते", "hi-IN")
    assert tried == ["hi-IN-SwaraNeural", "multi"] and voice == speech.settings.speech_voice and audio == b"mp3"


def test_formatting_is_not_read_aloud():
    raw = "Options:\n\n1. **SGG Antelio Plus** - solar control 【4:0†source】\n- [Cool-lite](http://x) colours"
    assert speech.speakable(raw) == "Options: SGG Antelio Plus - solar control Cool-lite colours"


def test_speech_request_shape(azure_tts):
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), headers=request.headers, body=request.content.decode())
        return httpx.Response(200, content=b"mp3")

    azure_tts(handler)
    assert speech.synthesize("Bonjour", "fr-FR") == b"mp3"
    assert seen["url"] == "https://eastus2.tts.speech.microsoft.com/cognitiveservices/v1"
    assert seen["headers"]["Ocp-Apim-Subscription-Key"] == "k" and "fr-FR" in seen["body"]
    assert seen["headers"]["X-Microsoft-OutputFormat"] == speech.OUTPUT_FORMAT


def test_speech_reuses_one_connection_client(azure_tts):
    azure_tts(lambda request: httpx.Response(200, content=b"mp3"))
    first = speech.http()
    speech.synthesize("one", "en-IN")
    speech.synthesize("two", "en-IN")
    assert speech.http() is first                       # same client, so its open connection is reused


def test_audio_is_passed_on_chunk_by_chunk(azure_tts):
    chunks = [b"ID3-first", b"-second", b"-third"]

    class Slow(httpx.SyncByteStream):
        def __iter__(self):
            yield from chunks

    azure_tts(lambda request: httpx.Response(200, stream=Slow()))
    stream = speech.synthesize_stream("Hello there", "en-IN")
    assert stream.first_chunk() == b"ID3-first"         # available before the rest has been read
    assert "first_audio" in stream.timing and stream.timing["first_audio"] >= stream.timing["azure_headers"]
    assert b"".join(stream) == b"".join(chunks)


def test_speak_streams_with_timing_headers(client, monkeypatch):
    monkeypatch.setattr(speech, "synthesize_stream", fake_stream(b"mp3-bytes", "en-IN-NeerjaNeural"))
    r = client.post("/api/speak", json={"text": "Hello", "lang": "en-IN"})
    assert r.status_code == 200 and r.content == b"mp3-bytes"
    assert "tts_first_audio;dur=" in r.headers["server-timing"] and r.headers["x-voice"] == "en-IN-NeerjaNeural"


def test_chat_reports_llm_time(client, monkeypatch):
    monkeypatch.setattr(foundry_agent, "ask", lambda cid, text, history=None, agent=None: foundry_agent.Answer("Hi"))
    r = client.post("/api/chat", json={"conversation_id": "c", "text": "hi"})
    assert r.json()["reply"] == "Hi" and isinstance(r.json()["timing"]["llm_ms"], int)
    assert r.headers["server-timing"].startswith("llm;dur=")


def test_password_protects_everything_but_health(monkeypatch):
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, app_password="s3cret"))
    c = TestClient(main.app)
    assert c.get("/").status_code == 401 and "Basic" in c.get("/").headers["www-authenticate"]
    assert c.post("/api/conversation").status_code == 401
    assert c.get("/", auth=("anyone", "wrong")).status_code == 401
    assert c.get("/", auth=("anyone", "s3cret")).status_code == 200
    assert c.get("/health").status_code == 200


def test_no_password_means_open(client):
    assert client.get("/").status_code == 200


def test_server_never_tries_a_browser_sign_in(monkeypatch):
    seen = {}

    class FakeCred:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(foundry_agent, "DefaultAzureCredential", FakeCred)
    for on_server in (True, False):
        foundry_agent.credential.cache_clear()
        monkeypatch.setattr(foundry_agent, "settings", dataclasses.replace(foundry_agent.settings, on_server=on_server))
        foundry_agent.credential()
        assert seen["exclude_interactive_browser_credential"] is on_server
    foundry_agent.credential.cache_clear()


def test_rejected_voice_is_replaced_from_azures_voice_list(monkeypatch, azure_tts):
    calls = []

    def handler(request):
        body = request.content.decode()
        calls.append(body)
        return httpx.Response(200 if "or-IN-RealVoiceNeural" in body else 400, content=b"mp3")

    catalogue = [{"ShortName": "or-IN-MaleVoiceNeural", "Locale": "or-IN", "Gender": "Male"},
                 {"ShortName": "or-IN-RealVoiceNeural", "Locale": "or-IN", "Gender": "Female"},
                 {"ShortName": "hi-IN-SwaraNeural", "Locale": "hi-IN", "Gender": "Female"}]
    azure_tts(handler)
    monkeypatch.setattr(speech, "available_voices", lambda: catalogue)
    monkeypatch.setattr(speech, "_resolved", {})
    audio, voice = speech.synthesize_with_voice("ନମସ୍କାର", "or-IN")
    assert voice == "or-IN-RealVoiceNeural" and len(calls) == 2
    speech.synthesize_with_voice("ନମସ୍କାର", "or-IN")          # remembered: straight to the working voice
    assert len(calls) == 3 and "or-IN-RealVoiceNeural" in calls[-1]
    assert speech.pick_voice("ta-IN", catalogue) is None


def test_voice_follows_the_script_of_the_reply():
    hindi = "SGG Planitherm एक Low-E thermal insulation glass है, जो गर्मी को अंदर आने से रोकता है।"
    assert speech.language_of(hindi, "en-IN") == "hi-IN"           # English picked, Hindi reply -> Hindi voice
    assert speech.language_of(hindi, "mr-IN") == "mr-IN"           # Devanagari stays Marathi when Marathi is picked
    assert speech.language_of("Planitherm பற்றி சொல்லுங்கள்", "en-IN") == "ta-IN"
    assert speech.language_of("Planitherm is a Low-E glass.", "en-IN") == "en-IN"
    assert speech.language_of("Bonjour, le verre Planitherm", "fr-FR") == "fr-FR"
    assert speech.language_of("Use SGG Planitherm (प्लानिथर्म) for double glazing units today", "en-IN") == "en-IN"


def test_speak_reports_the_language_it_spoke(client, monkeypatch):
    monkeypatch.setattr(speech, "synthesize_stream", fake_stream(b"mp3"))
    r = client.post("/api/speak", json={"text": "यह ग्लास गर्मी रोकता है", "lang": "en-IN"})
    assert r.headers["x-lang"] == "hi-IN"


def test_speech_token_for_browser_recognition(client, monkeypatch):
    monkeypatch.setattr(speech, "issue_token", lambda: "tok123")
    assert client.get("/api/speech-token").json() == {"token": "tok123", "region": speech.settings.speech_region}

    def fail():
        raise speech.SpeechError("Azure Speech token request returned 401")
    monkeypatch.setattr(speech, "issue_token", fail)
    assert client.get("/api/speech-token").status_code == 502


def test_speech_sdk_is_served_with_the_page(client):
    assert "/static/vendor/speech-sdk-1.51.0.min.js" in client.get("/").text
    r = client.get("/static/vendor/speech-sdk-1.51.0.min.js")
    assert r.status_code == 200 and "SpeechSDK" in r.text


def test_rate_limit_gives_a_plain_message(client, monkeypatch):
    import httpx
    import openai

    def limited(*a, **k):
        raise openai.RateLimitError("Error code: 429 - rate_limit_exceeded", response=httpx.Response(
            429, request=httpx.Request("POST", "https://x")), body=None)
    monkeypatch.setattr(foundry_agent, "ask", limited)
    r = client.post("/api/chat", json={"conversation_id": "c", "text": "hi"})
    assert r.status_code == 429 and "tokens-per-minute" in r.json()["detail"]


def test_limits_dropped_if_the_agent_service_refuses_them(monkeypatch):
    import httpx
    import openai
    calls = []

    def create(**kw):
        calls.append(kw)
        if "max_output_tokens" in kw:
            raise openai.BadRequestError("Unsupported parameter: 'max_output_tokens'", response=httpx.Response(
                400, request=httpx.Request("POST", "https://x")), body=None)
        return NS(output_text="ok")

    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: NS(responses=NS(create=create)))
    monkeypatch.setattr(foundry_agent, "_limits_supported", True)
    assert foundry_agent.ask("c", "hi").text == "ok" and foundry_agent.ask("c", "again").text == "ok"
    assert [("max_output_tokens" in c) for c in calls] == [True, False, False]   # learns after the first refusal
    monkeypatch.setattr(foundry_agent, "_limits_supported", True)


def test_agent_client_fails_fast_instead_of_waiting_out_rate_limits(monkeypatch):
    import openai as oa

    class FakeProject:
        def __init__(self, **kw):
            pass

        def get_openai_client(self):
            return oa.OpenAI(api_key="x", base_url="https://example.invalid/v1")

    foundry_agent._openai_client.cache_clear()
    monkeypatch.setattr(foundry_agent, "AIProjectClient", FakeProject)
    monkeypatch.setattr(foundry_agent, "credential", lambda: None)
    monkeypatch.setattr(foundry_agent, "settings", dataclasses.replace(
        foundry_agent.settings, foundry_project_endpoint="https://r.services.ai.azure.com/api/projects/p",
        foundry_agent_name="a"))
    client = foundry_agent._openai_client()
    assert client.max_retries == 0 and client.timeout == foundry_agent.AGENT_TIMEOUT_S
    foundry_agent._openai_client.cache_clear()


def test_agent_timeout_is_a_readable_error(client, monkeypatch):
    import openai as oa

    def slow(*a, **k):
        raise oa.APITimeoutError(request=httpx.Request("POST", "https://x"))

    monkeypatch.setattr(foundry_agent, "ask", slow)
    r = client.post("/api/chat", json={"conversation_id": "c", "text": "hi"})
    assert r.status_code == 504 and "longer than" in r.json()["detail"]


def test_text_history_sends_only_what_was_said_and_no_stored_conversation(client, monkeypatch):
    sent = {}
    usage = NS(input_tokens=1200, input_tokens_details=NS(cached_tokens=900))
    fake = NS(responses=NS(create=lambda **kw: sent.update(kw) or NS(output_text="Yes, 1.8.", usage=usage)))
    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: fake)
    monkeypatch.setattr(foundry_agent, "_limits_supported", True)
    history = [{"role": "user", "content": "U-value of Icy Menthol?"},
               {"role": "assistant", "content": "1.8 for double glazing."}]
    r = client.post("/api/chat", json={"history": history, "text": "And single glazed?"})
    assert r.status_code == 200 and r.json()["reply"] == "Yes, 1.8."
    assert r.json()["tokens"] == {"input": 1200, "cached": 900}
    assert "conversation" not in sent
    as_items = [{"type": "message", **m} for m in history]
    assert sent["input"] == as_items + [{"type": "message", "role": "system", "content": foundry_agent.SEARCH_REMINDER},
                                        {"type": "message", "role": "user", "content": "And single glazed?"}]


def test_system_note_moves_into_the_turn_if_the_agent_service_refuses_it(monkeypatch):
    import openai
    calls = []

    def create(**kw):
        calls.append(kw["input"])
        if any(m["role"] == "system" for m in kw["input"]):
            raise openai.BadRequestError("Error code: 400 - {'message': \"Invalid value: ''.\", 'param': 'input[1]'}", response=httpx.Response(
                400, request=httpx.Request("POST", "https://x")), body={"param": "input[1]"})
        return NS(output_text="0.43", usage=None)

    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: NS(responses=NS(create=create)))
    monkeypatch.setattr(foundry_agent, "_limits_supported", True)
    monkeypatch.setattr(foundry_agent, "_system_note_supported", True)
    assert foundry_agent.ask(None, "Graphite single?", []).text == "0.43"
    assert foundry_agent.ask(None, "And double?", []).text == "0.43"
    assert len(calls) == 3                                   # learns after the first refusal
    assert calls[-1] == [{"role": "user",
                          "content": "And double?\n\n(" + foundry_agent.SEARCH_REMINDER + ")"}]


def test_chat_needs_history_or_a_conversation(client):
    assert client.post("/api/chat", json={"text": "hi"}).status_code == 422
    assert client.post("/api/chat", json={"history": [{"role": "system", "content": "x"}], "text": "hi"}).status_code == 422


def test_english_uses_ava_dragon_hd_and_falls_back_to_an_indian_english_voice(azure_tts, monkeypatch):
    assert speech.voice_for("en-IN") == "en-US-Ava:DragonHDLatestNeural"
    asked = []

    def handler(request):
        if request.url.path.endswith("/voices/list"):
            return httpx.Response(200, json=[{"ShortName": "en-IN-NeerjaNeural", "Locale": "en-IN", "Gender": "Female"}])
        voice = request.content.decode().split("<voice name=")[1].split(">")[0].strip("'\"")
        asked.append(voice)
        return httpx.Response(400 if "DragonHD" in voice else 200, content=b"ID3")

    azure_tts(handler)
    monkeypatch.setattr(speech, "_voice_list", None)
    monkeypatch.setattr(speech, "_resolved", {})
    stream = speech.synthesize_stream("Hello there", "en-IN")
    assert stream.voice == "en-IN-NeerjaNeural" and asked == ["en-US-Ava:DragonHDLatestNeural", "en-IN-NeerjaNeural"]


def test_english_reply_gets_an_english_voice_even_when_hindi_is_picked():
    reply = "The solar factor of Cool Lite Graphite (ST 136) as single glazing is 0.43."
    assert speech.language_of(reply, "hi-IN") == "en-IN"
    assert speech.language_of(reply, "es-ES") == "es-ES"          # a Latin-script language stays as picked
    assert speech.language_of("यह ग्लास गर्मी रोकता है", "hi-IN") == "hi-IN"


def test_product_codes_are_spoken_letter_by_letter():
    assert speech.speakable("Graphite (ST 136) and Shine KS 138 II") == "Graphite (S T 136) and Shine K S 138 two"
    assert speech.speakable("U-value 1.8 for PLT T") == "U-value 1.8 for PLT T"


def test_search_tool_424_is_retried_once(monkeypatch):
    import openai
    calls = []

    def create(**kw):
        calls.append(kw)
        if len(calls) < 3:
            raise openai.APIStatusError("Error code: 424 - tool_server_error", response=httpx.Response(
                424, request=httpx.Request("POST", "https://x")), body=None)
        return NS(output_text="0.43", usage=None)

    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: NS(responses=NS(create=create)))
    monkeypatch.setattr(foundry_agent, "_limits_supported", True)
    with pytest.raises(openai.APIStatusError):                 # fails twice in a row: no third try
        foundry_agent.ask(None, "Graphite single?", [])
    assert len(calls) == 2
    assert foundry_agent.ask(None, "Graphite single?", []).text == "0.43" and len(calls) == 3


def test_page_can_pick_the_file_search_agent_and_nothing_else(client, monkeypatch):
    sent = []
    fake = NS(responses=NS(create=lambda **kw: sent.append(kw) or NS(output_text="ok", usage=None)))
    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: fake)
    monkeypatch.setattr(foundry_agent, "_limits_supported", True)
    monkeypatch.setattr(foundry_agent, "settings", dataclasses.replace(
        foundry_agent.settings, foundry_agent_name="clearview-glass-agent",
        other_agents="clearview-glass-agent-filesearch"))
    r = client.post("/api/chat", json={"conversation_id": "c", "text": "hi", "agent": "clearview-glass-agent-filesearch"})
    assert r.status_code == 200
    assert sent[-1]["extra_body"]["agent_reference"]["name"] == "clearview-glass-agent-filesearch"
    client.post("/api/chat", json={"conversation_id": "c", "text": "hi"})
    assert sent[-1]["extra_body"]["agent_reference"]["name"] == "clearview-glass-agent"
    assert client.post("/api/chat", json={"conversation_id": "c", "text": "hi", "agent": "someone-else"}).status_code == 400


def test_trim_deletes_search_items_and_keeps_the_conversation_text(client, monkeypatch):
    items = [NS(id="msg_3", type="message"), NS(id="ais_2", type="azure_ai_search_call"),
             NS(id="out_2", type="function_call_output"), NS(id="msg_1", type="message")]
    deleted = []
    fake = NS(conversations=NS(items=NS(
        list=lambda cid, **kw: items if cid == "conv_abc" else [],
        delete=lambda item_id, conversation_id: deleted.append((conversation_id, item_id)))))
    fake.with_options = lambda **kw: fake
    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: fake)
    r = client.post("/api/trim", json={"conversation_id": "conv_abc"})
    assert r.status_code == 200 and r.json()["removed"] == 2 and r.json()["kept"] == 2
    assert sorted(deleted) == [("conv_abc", "ais_2"), ("conv_abc", "out_2")]
    assert client.post("/api/trim", json={"conversation_id": "../etc"}).status_code == 422


def test_trim_failure_is_reported_not_fatal(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("delete not supported")
    fake = NS(conversations=NS(items=NS(list=boom)))
    fake.with_options = lambda **kw: fake
    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: fake)
    r = client.post("/api/trim", json={"conversation_id": "conv_abc"})
    assert r.status_code == 502 and "delete not supported" in r.json()["detail"]


def test_foundry_408_is_retried_once_when_starting_a_conversation(monkeypatch):
    import openai
    calls = []

    def create():
        calls.append(1)
        if len(calls) == 1:
            raise openai.APIStatusError("Error code: 408 - Timeout", response=httpx.Response(
                408, request=httpx.Request("POST", "https://x")), body=None)
        return NS(id="conv_ok")

    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: NS(conversations=NS(create=create)))
    assert foundry_agent.new_conversation() == "conv_ok" and len(calls) == 2


def test_a_reply_that_states_a_price_is_replaced(client, monkeypatch):
    made_up = "Twilight Blue (KT 740) at ₹24 per unit area, Misty Blue at ₹31. Twilight Blue is the cheapest."
    monkeypatch.setattr(foundry_agent, "ask", lambda *a, **k: foundry_agent.Answer(made_up))
    r = client.post("/api/chat", json={"conversation_id": "c", "text": "cheapest?"}).json()
    assert r["guarded"] == "price" and "₹" not in r["reply"] and "call back" in r["reply"]
    for fine in ["The U-value is 1.8.", "Light transmission is 60%.", "Call 1800 1021 215", "KT 740 is a shade."]:
        assert main.guard_reply(fine) == (fine, None)
    assert main.guard_reply("इसकी कीमत 500 रुपये है")[1] == "price"
    assert main.guard_reply("It costs Rs. 450 a square foot")[1] == "price"


def test_trim_deletes_one_at_a_time_and_retries_a_412(client, monkeypatch):
    import openai
    items = [NS(id="a", type="file_search_call"), NS(id="b", type="message")]
    tries = []

    def delete(item_id, conversation_id):
        tries.append(item_id)
        if len(tries) == 1:
            raise openai.APIStatusError("Error code: 412 - precondition_failed", response=httpx.Response(
                412, request=httpx.Request("POST", "https://x")), body=None)

    fake = NS(conversations=NS(items=NS(list=lambda cid, **kw: items, delete=delete)))
    fake.with_options = lambda **kw: fake
    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: fake)
    monkeypatch.setattr(foundry_agent.time, "sleep", lambda s: None)
    r = client.post("/api/trim", json={"conversation_id": "conv_abc"})
    assert r.status_code == 200 and r.json()["removed"] == 1 and tries == ["a", "a"]


def test_trim_stops_when_its_time_is_up(client, monkeypatch):
    items = [NS(id=f"t{i}", type="remote_function_call") for i in range(3)]
    def slow_delete(item_id, conversation_id):          # the first delete uses up the whole time budget
        monkeypatch.setattr(foundry_agent, "TRIM_BUDGET_S", -1)
    fake = NS(conversations=NS(items=NS(list=lambda cid, **kw: items, delete=slow_delete)))
    fake.with_options = lambda **kw: fake
    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: fake)
    r = client.post("/api/trim", json={"conversation_id": "conv_abc"}).json()
    assert r["removed"] == 1 and r["left"] == 2


def test_a_figure_without_a_search_this_turn_is_not_spoken(client, monkeypatch):
    removed = []
    made_up = foundry_agent.Answer("For SGG Nano Silver in double glazing, the U-value is 1.0 watts per square metre "
                                   "kelvin.", searched=False, message_ids=("msg_9",))
    monkeypatch.setattr(foundry_agent, "ask", lambda *a, **k: made_up)
    monkeypatch.setattr(foundry_agent, "remove_items", lambda cid, ids: removed.append((cid, ids)))
    monkeypatch.setattr(foundry_agent, "ask_searching", lambda *a, **k: None)   # service refuses a required search
    r = client.post("/api/chat", json={"conversation_id": "conv_x", "text": "Nano Silver."}).json()
    assert r["guarded"] == "figure without a search" and "1.0" not in r["reply"]
    assert removed == [("conv_x", ("msg_9",))]
    searched = foundry_agent.Answer("Nano Silver Shine (KS 138 II) in double glazing has a U-value of 1.6.", searched=True)
    monkeypatch.setattr(foundry_agent, "ask", lambda *a, **k: searched)
    assert client.post("/api/chat", json={"conversation_id": "conv_x", "text": "Shine?"}).json()["guarded"] is None
    assert main.guard_reply("The U-value is 1.6.", False, "Sorry, can you repeat that?")[1] is None   # a repeat
    assert main.guard_reply("Do you mean single or double glazing?", False)[1] is None              # no figure
    assert main.guard_reply("You're welcome. Thank you for calling.", False)[1] is None


def test_stored_turns_carry_the_reminder_and_report_the_search(monkeypatch):
    sent = {}
    out = [NS(type="remote_function_call", id="fc_1"), NS(type="message", id="msg_2")]
    fake = NS(responses=NS(create=lambda **kw: sent.update(kw) or NS(output_text="1.6", usage=None, output=out)))
    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: fake)
    monkeypatch.setattr(foundry_agent, "_limits_supported", True)
    monkeypatch.setattr(foundry_agent, "_system_note_supported", True)
    a = foundry_agent.ask("conv_x", "Nano Silver Shine U-value?")
    assert a.searched is True and a.message_ids == ("msg_2",)
    assert sent["conversation"] == "conv_x" and sent["input"][0]["role"] == "system"
    assert sent["input"][-1] == {"type": "message", "role": "user", "content": "Nano Silver Shine U-value?"}


def test_trim_also_removes_the_reminder(client, monkeypatch):
    items = [NS(id="u", type="message", role="user"), NS(id="s", type="message", role="system"),
             NS(id="a", type="message", role="assistant"), NS(id="f", type="remote_function_call")]
    deleted = []
    fake = NS(conversations=NS(items=NS(list=lambda cid, **kw: items,
                                        delete=lambda item_id, conversation_id: deleted.append(item_id))))
    fake.with_options = lambda **kw: fake
    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: fake)
    assert client.post("/api/trim", json={"conversation_id": "conv_abc"}).json()["removed"] == 2
    assert sorted(deleted) == ["f", "s"]


def test_an_unsearched_figure_is_answered_again_with_a_search(client, monkeypatch):
    made_up = foundry_agent.Answer("Envision Lumina lets through 60% in double glazing.", 2224, 2048, False, ("m1",))
    searched = foundry_agent.Answer("Envision Lumina (SKN 165 II) lets through 60% in a double glazed unit.",
                                    3300, 2048, True)
    monkeypatch.setattr(foundry_agent, "ask", lambda *a, **k: made_up)
    monkeypatch.setattr(foundry_agent, "remove_items", lambda *a: None)
    monkeypatch.setattr(foundry_agent, "ask_searching", lambda *a, **k: searched)
    r = client.post("/api/chat", json={"conversation_id": "conv_x", "text": "Lumina?"}).json()
    assert r["reply"] == searched.text and r["guarded"].startswith("figure without a search, answered again")
    assert r["tokens"]["input"] == 2224 + 3300


def test_required_search_is_sent_as_tool_choice(monkeypatch):
    sent = {}
    out = [NS(type="remote_function_call", id="fc"), NS(type="message", id="m")]
    fake = NS(responses=NS(create=lambda **kw: sent.update(kw) or NS(output_text="60%", usage=None, output=out)))
    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: fake)
    monkeypatch.setattr(foundry_agent, "_limits_supported", True)
    monkeypatch.setattr(foundry_agent, "_force_supported", True)
    a = foundry_agent.ask_searching("conv_x", "Lumina?")
    assert a.searched and sent["tool_choice"] == "required" and sent["conversation"] == "conv_x"
    assert [m["role"] for m in sent["input"]] == ["system"]       # the question is already in the conversation


def _items_client(items, deleted, fail_on=None):
    def delete(item_id, conversation_id):
        if item_id == fail_on:
            raise openai_timeout()
        deleted.append(item_id)
        items[:] = [i for i in items if i.id != item_id]
    fake = NS(conversations=NS(items=NS(list=lambda cid, **kw: list(items), delete=delete)))
    fake.with_options = lambda **kw: fake
    return fake


def openai_timeout():
    import openai
    return openai.APITimeoutError(request=httpx.Request("POST", "https://x"))


def test_trim_deletes_a_search_call_and_its_output_together(client, monkeypatch):
    items = [NS(id="out1", type="remote_function_call_output", call_id="c1"),
             NS(id="call1", type="remote_function_call", call_id="c1"),
             NS(id="note", type="message", role="system"), NS(id="ans", type="message", role="assistant")]
    deleted = []
    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: _items_client(items, deleted))
    r = client.post("/api/trim", json={"conversation_id": "conv_abc"}).json()
    assert r["removed"] == 3 and r["left"] == 0 and deleted.index("out1") < deleted.index("call1")


def test_a_half_deleted_pair_is_repaired_first(client, monkeypatch):
    items = [NS(id="call1", type="remote_function_call", call_id="c1"),      # its output is already gone
             NS(id="ans", type="message", role="assistant")]
    deleted = []
    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: _items_client(items, deleted))
    r = client.post("/api/trim", json={"conversation_id": "conv_abc"}).json()
    assert deleted == ["call1"] and r["repaired"] == 1


def test_a_turn_refused_for_half_a_pair_is_repaired_and_asked_again(monkeypatch):
    import openai
    calls, deleted = [], []
    items = [NS(id="call9", type="remote_function_call", call_id="call_09f5")]

    def create(**kw):
        calls.append(kw)
        if len(calls) == 1:
            raise openai.BadRequestError("Error code: 400 - No tool output found for remote function call call_09f5.",
                                         response=httpx.Response(400, request=httpx.Request("POST", "https://x")),
                                         body=None)
        return NS(output_text="Xtreme 70/33 II, 69%.", usage=None, output=[])

    fake = _items_client(items, deleted)
    fake.responses = NS(create=create)
    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: fake)
    monkeypatch.setattr(foundry_agent, "_limits_supported", True)
    monkeypatch.setattr(foundry_agent, "_system_note_supported", True)
    assert foundry_agent.ask("conv_x", "Which Xtreme lets in the most light?").text.startswith("Xtreme")
    assert deleted == ["call9"] and len(calls) == 2


def test_without_call_ids_the_refused_turn_clears_all_search_items(monkeypatch):
    import openai
    calls, deleted = [], []
    items = [NS(id="call9", type="remote_function_call"), NS(id="ans", type="message", role="assistant")]

    def create(**kw):
        calls.append(kw)
        if len(calls) == 1:
            raise openai.BadRequestError("No tool output found for remote function call call_09f5.",
                                         response=httpx.Response(400, request=httpx.Request("POST", "https://x")),
                                         body=None)
        return NS(output_text="ok", usage=None, output=[])

    fake = _items_client(items, deleted)
    fake.responses = NS(create=create)
    monkeypatch.setattr(foundry_agent, "_openai_client", lambda: fake)
    monkeypatch.setattr(foundry_agent, "_limits_supported", True)
    monkeypatch.setattr(foundry_agent, "_system_note_supported", True)
    assert foundry_agent.ask("conv_x", "hi").text == "ok" and deleted == ["call9"]
