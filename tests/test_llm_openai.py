import json
from types import SimpleNamespace as NS

from app import agent
from app.llm_openai import AzureOpenAIClient, from_openai_response, to_openai_messages, to_openai_tools
from app.tools import TOOLS


def _completion(content=None, tool_calls=(), finish="stop"):
    calls = [NS(id=c[0], function=NS(name=c[1], arguments=c[2])) for c in tool_calls]
    return NS(choices=[NS(message=NS(content=content, tool_calls=calls or None), finish_reason=finish)],
              usage={"prompt_tokens": 10, "completion_tokens": 5})


def test_history_translates_to_chat_completions():
    history = [
        {"role": "user", "content": "Water is coming through my ceiling"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Sorry to hear that."},
            {"type": "tool_use", "id": "call_1", "name": "escalate_to_contractor", "input": {"urgency": "emergency"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": '{"ok": true}', "is_error": False},
            {"type": "text", "text": "Thanks"}]},
        {"role": "assistant", "content": "Fallback line"},
    ]
    system = [{"type": "text", "text": "Rules"}, {"type": "text", "text": "<company_information/>"}]
    out = to_openai_messages(system, history)

    assert out[0] == {"role": "system", "content": "Rules\n\n<company_information/>"}
    assert out[1] == {"role": "user", "content": "Water is coming through my ceiling"}
    assert out[2]["role"] == "assistant" and out[2]["content"] == "Sorry to hear that."
    assert out[2]["tool_calls"][0]["function"] == {"name": "escalate_to_contractor",
                                                   "arguments": json.dumps({"urgency": "emergency"})}
    # the tool result must come straight after the assistant's tool call, then the caller's words
    assert out[3] == {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'}
    assert out[4] == {"role": "user", "content": "Thanks"}
    assert out[5] == {"role": "assistant", "content": "Fallback line"}


def test_tools_keep_their_schemas():
    converted = to_openai_tools(TOOLS)
    assert [t["function"]["name"] for t in converted] == [t["name"] for t in TOOLS]
    assert converted[0]["function"]["parameters"] == TOOLS[0]["input_schema"]


def test_response_becomes_text_and_tool_use_blocks():
    resp = from_openai_response(_completion("Let me check.", [("call_9", "check_availability", '{"day": "mon"}')],
                                            finish="tool_calls"))
    assert resp.stop_reason == "tool_use"
    assert [b.to_dict() for b in resp.content] == [
        {"type": "text", "text": "Let me check."},
        {"type": "tool_use", "id": "call_9", "name": "check_availability", "input": {"day": "mon"}}]

    assert from_openai_response(_completion("Bye.")).stop_reason == "end_turn"
    assert from_openai_response(_completion("Cut o", finish="length")).stop_reason == "max_tokens"
    bad = from_openai_response(_completion(None, [("c", "end_call", "{not json")]))
    assert bad.content[0].input == {}  # tool validation then reports the error back to the model


def test_full_call_turn_runs_through_gpt_adapter(contractor, monkeypatch):
    """The real agent loop, tools and DB, with Azure OpenAI replaced by a scripted fake."""
    script = [
        _completion("I'm sorry, I'm alerting the team now.", [(
            "call_1", "escalate_to_contractor", json.dumps({
                "urgency": "emergency", "reason": "Active leak", "callback_number": "+15555550123",
                "details": "Water through kitchen ceiling", "caller_name": "Jo"}))], finish="tool_calls"),
        _completion("Someone will call you back shortly. Goodbye.", [(
            "call_2", "end_call", json.dumps({"outcome": "escalated", "summary": "Leak escalated."}))],
            finish="tool_calls"),
    ]
    sent = []

    def create(**kwargs):
        sent.append(kwargs)
        return script.pop(0)

    client = AzureOpenAIClient("demo-res", "test-key")
    monkeypatch.setattr(client._client.chat.completions, "create", create)
    monkeypatch.setattr(agent, "client", lambda: client)

    call_id = agent.start_call(contractor["id"], "simulator", caller_number="+15555550123")["call_id"]
    out = agent.handle_turn(call_id, "Water is pouring through my kitchen ceiling!")

    assert [a["tool"] for a in out["actions"]] == ["escalate_to_contractor", "end_call"]
    assert all(a["ok"] for a in out["actions"]) and out["ended"] is True
    assert "alerting the team" in out["say"] and "Goodbye" in out["say"]
    assert agent.get_call(call_id)["outcome"] == "escalated"
    second = sent[1]["messages"]
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "call_1"
    assert "cache_control" not in sent[0] and "thinking" not in sent[0]


def test_endpoint_from_portal_wins_over_resource_name():
    from app.llm_openai import openai_base_url

    built = "https://voice-agent-gurpreet.openai.azure.com/openai/v1/"
    assert openai_base_url("voice-agent-gurpreet") == built
    for pasted in ("https://voice-agent-gurpreet.openai.azure.com/openai/v1",
                   "https://voice-agent-gurpreet.openai.azure.com/openai/v1/",
                   "https://voice-agent-gurpreet.openai.azure.com/openai/",
                   "https://voice-agent-gurpreet.openai.azure.com"):
        assert openai_base_url("ignored-name", pasted) == built
    assert openai_base_url("x", "https://my-proxy.example.com/openai/v1") == "https://my-proxy.example.com/openai/v1/"
    assert AzureOpenAIClient("x", "k", endpoint="https://custom.example.com").base_url == \
        "https://custom.example.com/openai/v1/"
