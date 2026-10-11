import dataclasses
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import foundry_agent, main, speech


@pytest.fixture
def client():
    return TestClient(main.app)


def test_page_has_mic_language_picker_and_text_box(client):
    html = client.get("/").text
    for needle in ('id="mic"', 'id="lang"', 'value="hi-IN"', 'id="typed"', "/api/speak"):
        assert needle in html


def test_conversation_chat_and_speak_go_to_foundry_and_speech(client, monkeypatch):
    calls = []
    monkeypatch.setattr(foundry_agent, "new_conversation", lambda: "conv_123")
    monkeypatch.setattr(foundry_agent, "ask", lambda cid, text: calls.append((cid, text)) or "Namaste! Kaise madad karun?")
    monkeypatch.setattr(speech, "synthesize", lambda text, lang=None: b"ID3fake-mp3")

    cid = client.post("/api/conversation").json()["conversation_id"]
    reply = client.post("/api/chat", json={"conversation_id": cid, "text": "Mera chhat tapak raha hai"}).json()["reply"]
    audio = client.post("/api/speak", json={"text": reply, "lang": "hi-IN"})

    assert cid == "conv_123" and calls == [("conv_123", "Mera chhat tapak raha hai")]
    assert reply.startswith("Namaste")
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg" and audio.content == b"ID3fake-mp3"


def test_failures_return_readable_errors(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("PermissionDenied")
    monkeypatch.setattr(foundry_agent, "ask", boom)
    monkeypatch.setattr(speech, "synthesize", boom)
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
    assert foundry_agent.ask("conv_9", "hi") == "Hello there."
    assert sent == {"conversation": "conv_9", "input": "hi",
                    "extra_body": {"agent_reference": {"name": "clearview-glass-agent", "type": "agent_reference"}}}


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


def test_speech_request_shape(monkeypatch):
    seen = {}

    def fake_post(url, content, timeout, headers):
        seen.update(url=url, headers=headers, body=content.decode())
        return NS(status_code=200, content=b"mp3", text="", reason_phrase="OK")

    monkeypatch.setattr(speech, "settings", dataclasses.replace(speech.settings, speech_key="k", speech_region="eastus2"))
    monkeypatch.setattr(speech.httpx, "post", fake_post)
    assert speech.synthesize("Bonjour", "fr-FR") == b"mp3"
    assert seen["url"] == "https://eastus2.tts.speech.microsoft.com/cognitiveservices/v1"
    assert seen["headers"]["Ocp-Apim-Subscription-Key"] == "k" and "fr-FR" in seen["body"]


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
