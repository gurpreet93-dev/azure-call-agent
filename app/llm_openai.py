"""GPT on Azure OpenAI (Microsoft Foundry), behind the same interface the agent already uses.

The agent loop is written against Claude's Messages API: `client.messages.create(...)` returns
content blocks (`text`, `tool_use`) and a `stop_reason`, and history is stored in that format.
This adapter translates each request to Chat Completions and each reply back, so app/agent.py,
the tools, the stored transcripts and the tests don't change when you switch providers.
"""
import json
from dataclasses import dataclass, field
from typing import Any

import openai


@dataclass
class Block:
    type: str                      # "text" | "tool_use"
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        if self.type == "text":
            return {"type": "text", "text": self.text}
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


@dataclass
class Response:
    content: list[Block]
    stop_reason: str               # "tool_use" | "end_turn" | "max_tokens"
    usage: Any = None


def _system_text(system: str | list[dict] | None) -> str:
    if not system:
        return ""
    if isinstance(system, str):
        return system
    return "\n\n".join(b["text"] for b in system if b.get("type") == "text")


def to_openai_messages(system, messages: list[dict]) -> list[dict]:
    """Claude-format history -> Chat Completions messages."""
    out: list[dict] = []
    if text := _system_text(system):
        out.append({"role": "system", "content": text})
    for m in messages:
        content = m["content"]
        if m["role"] == "user":
            if isinstance(content, str):
                out.append({"role": "user", "content": content})
                continue
            texts = []
            for b in content:  # tool results must directly follow the assistant's tool calls
                if b["type"] == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": str(b["content"])})
                elif b["type"] == "text":
                    texts.append(b["text"])
            if texts:
                out.append({"role": "user", "content": "\n\n".join(texts)})
        else:
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content})
                continue
            text = " ".join(b["text"] for b in content if b["type"] == "text").strip()
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                     for b in content if b["type"] == "tool_use"]
            msg: dict = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
    return out


def to_openai_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t["input_schema"]}} for t in tools]


def from_openai_response(resp) -> Response:
    choice = resp.choices[0]
    msg = choice.message
    blocks: list[Block] = []
    if msg.content and msg.content.strip():
        blocks.append(Block(type="text", text=msg.content))
    for call in msg.tool_calls or []:
        try:
            args = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}  # the tool's own validation then returns an error the model can recover from
        blocks.append(Block(type="tool_use", id=call.id, name=call.function.name,
                            input=args if isinstance(args, dict) else {}))
    if any(b.type == "tool_use" for b in blocks):
        stop = "tool_use"
    elif choice.finish_reason == "length":
        stop = "max_tokens"
    else:
        stop = "end_turn"
    return Response(content=blocks, stop_reason=stop, usage=resp.usage)


class _Messages:
    def __init__(self, client: openai.OpenAI):
        self._client = client

    def create(self, *, model: str, max_tokens: int, messages: list[dict], system=None, tools=None,
               **_ignored) -> Response:
        # _ignored: Claude-only options (cache_control, thinking, output_config). Azure OpenAI
        # caches long repeated prompt prefixes automatically.
        kwargs: dict = {"model": model, "max_completion_tokens": max_tokens,
                        "messages": to_openai_messages(system, messages)}
        if oa_tools := to_openai_tools(tools):
            kwargs["tools"] = oa_tools
        return from_openai_response(self._client.chat.completions.create(**kwargs))


def openai_base_url(resource: str, endpoint: str = "") -> str:
    """The v1 API base URL. Uses the endpoint copied from the portal when given (with or without
    the /openai/v1 suffix); otherwise Azure's standard address for the resource name."""
    if endpoint:
        url = endpoint.rstrip("/")
        if not url.endswith("/openai/v1"):
            url = url.removesuffix("/openai") + "/openai/v1"
        return url + "/"
    return f"https://{resource}.openai.azure.com/openai/v1/"


class AzureOpenAIClient:
    """Exposes `.messages.create(...)` like the Anthropic SDK clients."""

    def __init__(self, resource: str, api_key: str, endpoint: str = "", max_retries: int = 2, timeout: float = 30):
        self.base_url = openai_base_url(resource, endpoint)
        self._client = openai.OpenAI(base_url=self.base_url, api_key=api_key,
                                     max_retries=max_retries, timeout=timeout)
        self.messages = _Messages(self._client)
