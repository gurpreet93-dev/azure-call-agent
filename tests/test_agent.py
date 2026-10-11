"""Drive the agent loop with a scripted fake model to check the plumbing (not model quality -
that's what the scenario eval is for)."""
import json

from anthropic.types import Message

from app import agent
from app.db import get_db


def _msg(content, stop="end_turn"):
    return Message.model_validate({
        "id": "msg_x", "type": "message", "role": "assistant", "model": "claude-haiku-4-5",
        "content": content, "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5}})


class FakeClient:
    def __init__(self, script):
        self.script, self.requests = list(script), []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        return self.script.pop(0)


def test_emergency_call_escalates_then_ends(contractor, monkeypatch):
    fake = FakeClient([
        _msg([{"type": "text", "text": "I'm sorry to hear that. I'm alerting the team now."},
              {"type": "tool_use", "id": "t1", "name": "escalate_to_contractor", "input": {
                  "urgency": "emergency", "reason": "Active leak", "callback_number": "+15555550123",
                  "details": "Water through kitchen ceiling since 6am", "caller_name": "Jo"}}], stop="tool_use"),
        _msg([{"type": "text", "text": "The team will call you back as soon as possible. Goodbye."},
              {"type": "tool_use", "id": "t2", "name": "end_call", "input": {
                  "outcome": "escalated", "summary": "Active kitchen leak, escalated."}}], stop="tool_use"),
    ])
    monkeypatch.setattr(agent, "client", lambda: fake)
    call_id = agent.start_call(contractor["id"], "simulator", caller_number="+15555550123")["call_id"]
    out = agent.handle_turn(call_id, "Water is pouring through my kitchen ceiling!")

    assert out["ended"] is True
    assert [a["tool"] for a in out["actions"]] == ["escalate_to_contractor", "end_call"]
    assert "alerting the team" in out["say"] and "Goodbye" in out["say"]
    call = agent.get_call(call_id)
    assert call["status"] == "completed" and call["outcome"] == "escalated"
    with get_db() as db:
        alert = db.execute("SELECT * FROM outbox WHERE to_addr='owner@example.com'").fetchone()
    assert alert and alert["subject"].startswith("[URGENT]")

    first = fake.requests[0]
    assert first["model"] == agent.settings.agent_model
    assert {k: first[k] for k in agent.model_options()} == agent.model_options()  # thinking/effort sent
    assert first["cache_control"] == {"type": "ephemeral"}
    assert "Caller ID: +15555550123" in first["messages"][0]["content"]
    # full-context mode: KB lives in the system prompt, search tool is not offered
    assert any("company_information" in b["text"] for b in first["system"])
    assert "search_knowledge_base" not in [t["name"] for t in first["tools"]]
    # second request carries the tool result for the first
    assert fake.requests[1]["messages"][-1]["content"][0]["tool_use_id"] == "t1"


def test_history_is_append_only_across_turns(contractor, monkeypatch):
    fake = FakeClient([_msg([{"type": "text", "text": "Sure, what's the address?"}]),
                       _msg([{"type": "text", "text": "Thanks."}])])
    monkeypatch.setattr(agent, "client", lambda: fake)
    call_id = agent.start_call(contractor["id"], "simulator")["call_id"]
    agent.handle_turn(call_id, "I need an inspection")
    agent.handle_turn(call_id, "12 Oak Street")
    turn1, turn2 = fake.requests[0]["messages"], fake.requests[1]["messages"]
    assert turn2[:len(turn1)] == turn1  # earlier history unchanged -> prompt cache stays valid
    assert turn2[-1] == {"role": "user", "content": "12 Oak Street"}


def test_api_failure_gives_graceful_spoken_fallback(contractor, monkeypatch):
    import anthropic
    import httpx

    class Broken:
        messages = None
        def __init__(self): self.messages = self
        def create(self, **kw):
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    monkeypatch.setattr(agent, "client", lambda: Broken())
    call_id = agent.start_call(contractor["id"], "simulator")["call_id"]
    out = agent.handle_turn(call_id, "Hello?")
    assert out["say"] == agent.FALLBACK_LINE and out["ended"] is False


def test_client_targets_foundry_or_anthropic_by_setting(monkeypatch):
    import dataclasses

    import anthropic

    foundry = dataclasses.replace(agent.settings, llm_provider="foundry", foundry_resource="demo-res",
                                  foundry_api_key="test-key")
    monkeypatch.setattr(agent, "settings", foundry)
    c = agent.make_client()
    assert isinstance(c, anthropic.AnthropicFoundry)
    assert str(c.base_url).startswith("https://demo-res.services.ai.azure.com/anthropic")

    monkeypatch.setattr(agent, "settings", dataclasses.replace(foundry, llm_provider="anthropic",
                                                               anthropic_api_key="sk-ant-test"))
    c = agent.make_client()
    assert type(c) is anthropic.Anthropic

    from app.llm_openai import AzureOpenAIClient
    monkeypatch.setattr(agent, "settings", dataclasses.replace(foundry, llm_provider="azure-openai"))
    c = agent.make_client()
    assert isinstance(c, AzureOpenAIClient)
    assert str(c._client.base_url) == "https://demo-res.openai.azure.com/openai/v1/"

    monkeypatch.setattr(agent, "settings", dataclasses.replace(foundry, llm_provider="openai"))
    import pytest
    with pytest.raises(ValueError):
        agent.make_client()


def test_model_options_follow_settings(monkeypatch):
    import dataclasses

    import pytest

    def opts(**kw):
        monkeypatch.setattr(agent, "settings", dataclasses.replace(agent.settings, llm_provider="foundry", **kw))
        return agent.model_options()

    assert opts(agent_thinking="off", agent_effort="low") == {
        "thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}
    assert opts(agent_thinking="adaptive", agent_effort="medium")["thinking"] == {"type": "adaptive"}
    assert opts(agent_thinking="model-default", agent_effort="") == {}  # Haiku 4.5: send nothing
    with pytest.raises(ValueError):
        opts(agent_thinking="maybe")
    monkeypatch.setattr(agent, "settings", dataclasses.replace(agent.settings, llm_provider="azure-openai",
                                                               agent_thinking="off", agent_effort="low"))
    assert agent.model_options() == {}  # Claude-only options are never sent to GPT
