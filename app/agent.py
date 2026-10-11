"""The call agent: one Claude tool-use loop per caller utterance.

The model is GPT on Azure OpenAI (LLM_PROVIDER=azure-openai, the default), Claude on Microsoft
Foundry (foundry) or Claude on the Anthropic API (anthropic). Every client exposes the same
`messages.create` interface (GPT through app/llm_openai.py), so the loop below is identical.

Channel-agnostic: the browser simulator and the Twilio phone webhook both call
`start_call` and `handle_turn`, and speak whatever text comes back.
"""
import json
import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import openai

from app import knowledge
from app.config import settings
from app.db import get_db, now_iso, row_to_dict
from app.tools import TOOLS, run_tool, spoken_time

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8          # safety valve: tool calls allowed for one caller utterance
MAX_TOKENS = 1024            # spoken replies are short; this only bounds runaway output
FALLBACK_LINE = ("Sorry, I'm having trouble on my end. I've noted your number and someone "
                 "from the team will call you back shortly.")

LLM_ERRORS = (anthropic.APIError, openai.APIError)

_client = None


def make_client():
    if settings.llm_provider == "azure-openai":
        from app.llm_openai import AzureOpenAIClient
        return AzureOpenAIClient(settings.foundry_resource, settings.foundry_api_key,
                                 endpoint=settings.azure_openai_endpoint, max_retries=2, timeout=30)
    if settings.llm_provider == "foundry":
        return anthropic.AnthropicFoundry(resource=settings.foundry_resource or None,
                                          api_key=settings.foundry_api_key or None, max_retries=2, timeout=30)
    if settings.llm_provider == "anthropic":
        return anthropic.Anthropic(api_key=settings.anthropic_api_key or None, max_retries=2, timeout=30)
    raise ValueError(f"LLM_PROVIDER must be azure-openai, foundry or anthropic, not {settings.llm_provider!r}")


def model_options() -> dict:
    """Thinking/effort parameters for every request, from AGENT_THINKING / AGENT_EFFORT (Claude only)."""
    opts: dict = {}
    if settings.llm_provider == "azure-openai":
        return opts
    if settings.agent_thinking == "off":
        opts["thinking"] = {"type": "disabled"}
    elif settings.agent_thinking == "adaptive":
        opts["thinking"] = {"type": "adaptive"}
    elif settings.agent_thinking != "model-default":
        raise ValueError(f"AGENT_THINKING must be off, adaptive or model-default, not {settings.agent_thinking!r}")
    if settings.agent_effort:
        opts["output_config"] = {"effort": settings.agent_effort}
    return opts


def client():
    global _client
    if _client is None:
        _client = make_client()
    return _client


# ---------------------------------------------------------------- prompt

INSTRUCTIONS = """You are the phone receptionist for {business}, a {trade}. You answer inbound calls for {owner}.

## How you speak
- This is a live phone call. Your words are converted to speech. Use short, natural sentences; one question at a time.
- No lists, markdown, emojis or URLs. Say prices like "four hundred and fifty dollars".
- Never mention tools, documents, the knowledge base or that you are reading anything.
- If the caller asks whether you are a person, say you are {business}'s virtual assistant.

## What you know
- Only state facts about the business that appear in the company information{kb_where}. If it's not there, say you'll have {owner} follow up. Never guess.
- Prices: quote ONLY prices that appear in an approved price list in the company information. Never estimate, round into a range, or invent a price.

## What you can do (choose what fits the call - there is no fixed script)
1. Answer questions from the company information.
2. Book a site visit/inspection: when the job needs eyes on site (replacements, metal roofs, uncertain leaks, larger repairs) or the caller asks. Use check_availability first; offer two or three times; never invent availability.
3. Send a written preliminary estimate: only when the job can be priced entirely from the approved price list and the caller wants a quote by email.
4. Escalate to {owner}: emergencies (active water coming in, safety risk) -> urgency "emergency", and ALSO offer the earliest inspection slot. Commercial jobs, complaints, warranty claims, discount requests -> "high". Anything you can't handle -> take a message ("normal"). Never promise a technician will arrive immediately.

## Collecting details
- Get the caller's name, best phone number, email, property address and what they need. Save them with update_caller_details as you learn them.
- Read the email back letter by letter and get a yes before using it. Read back the address and appointment time before booking.
- Don't ask for details you already have. The caller ID is a fine default phone number; confirm it.

## Ending
- When the caller's needs are handled, briefly confirm what happens next, say goodbye, and call end_call in the same turn.

Business timezone: {timezone}. Business hours: {hours}."""


def _hours_text(hours: dict) -> str:
    names = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}
    return ", ".join(f"{names[d]} {h[0]}-{h[1]}" if h else f"{names[d]} closed" for d, h in
                     ((d, hours.get(d)) for d in names))


def build_system(contractor: dict, mode: str) -> list[dict]:
    """Byte-stable for a given KB, so the cached prefix (tools + system) is reused every turn."""
    text = INSTRUCTIONS.format(
        business=contractor["business_name"], trade=contractor["trade"], owner=contractor["owner_name"],
        timezone=contractor["timezone"], hours=_hours_text(contractor["business_hours"]),
        kb_where=" below" if mode == "full_context" else " returned by search_knowledge_base (search before answering)",
    )
    blocks = [{"type": "text", "text": text}]
    if mode == "full_context":
        docs = knowledge.full_text(contractor["id"])
        kb = "\n\n".join(f'<document name="{d["filename"]}">\n{d["content"]}\n</document>' for d in docs)
        blocks.append({"type": "text", "text": f"<company_information>\n{kb or '(no documents uploaded yet)'}\n</company_information>"})
    return blocks


def tools_for(mode: str) -> list[dict]:
    if mode == "full_context":
        return [t for t in TOOLS if t["name"] != "search_knowledge_base"]
    return TOOLS


# ---------------------------------------------------------------- persistence

def get_contractor(contractor_id: int) -> dict | None:
    with get_db() as db:
        return row_to_dict(db.execute("SELECT * FROM contractors WHERE id=?", (contractor_id,)).fetchone())


def get_call(external_id: str) -> dict | None:
    with get_db() as db:
        return row_to_dict(db.execute("SELECT * FROM calls WHERE external_id=?", (external_id,)).fetchone())


def _log(call_id: int, role: str, content: str) -> None:
    with get_db() as db:
        db.execute("INSERT INTO transcript (call_id, role, content, created_at) VALUES (?,?,?,?)",
                   (call_id, role, content, now_iso()))


def greeting(contractor: dict) -> str:
    return (f"Thanks for calling {contractor['business_name']}, this is the virtual assistant. "
            f"How can I help you today?")


def start_call(contractor_id: int, channel: str, caller_number: str | None = None,
               external_id: str | None = None) -> dict:
    """Create the call and return the greeting. The greeting is fixed text, not generated,
    so the caller hears something instantly."""
    contractor = get_contractor(contractor_id)
    external_id = external_id or f"sim-{uuid.uuid4().hex[:12]}"
    with get_db() as db:
        db.execute("INSERT OR IGNORE INTO calls (contractor_id, external_id, channel, caller_number, started_at) "
                   "VALUES (?,?,?,?,?)", (contractor_id, external_id, channel, caller_number, now_iso()))
    call = get_call(external_id)
    line = greeting(contractor)
    _log(call["id"], "agent", line)
    return {"call_id": external_id, "say": line}


# ---------------------------------------------------------------- the loop

def handle_turn(external_id: str, utterance: str) -> dict:
    """Process one caller utterance. Returns {"say": str, "ended": bool, "actions": [...]}."""
    call = get_call(external_id)
    if call is None:
        raise ValueError("Unknown call")
    if call["status"] == "completed":
        return {"say": "", "ended": True, "actions": []}
    contractor = get_contractor(call["contractor_id"])
    mode = knowledge.kb_status(contractor["id"])["mode"]
    messages: list[dict] = json.loads(call["messages_json"])  # append-only history
    _log(call["id"], "caller", utterance)

    if not messages:
        now = datetime.now(ZoneInfo(contractor["timezone"]))
        local_now = f"{spoken_time(now)}, {now.year}"
        utterance = (f"[Call connected {local_now} business time. Caller ID: {call['caller_number'] or 'unknown'}. "
                     f'You already greeted the caller with: "{greeting(contractor)}"]\n\n{utterance}')
    if messages and messages[-1]["role"] == "user":  # previous turn ended on tool results
        messages[-1]["content"].append({"type": "text", "text": utterance})
    else:
        messages.append({"role": "user", "content": utterance})

    ctx = {"contractor": contractor, "call": call, "ended": False}
    spoken, actions = [], []
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            response = client().messages.create(
                model=settings.agent_model,
                max_tokens=MAX_TOKENS,
                system=build_system(contractor, mode),
                tools=tools_for(mode),
                messages=messages,
                cache_control={"type": "ephemeral"},  # caches tools + system + history prefix
                **model_options(),
            )
            log.info("usage call=%s %s", external_id, response.usage)
            messages.append({"role": "assistant", "content": [b.to_dict() for b in response.content]})
            spoken += [b.text for b in response.content if b.type == "text" and b.text.strip()]

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                break
            results = []
            for block in tool_uses:  # all results go back in ONE user message
                result, ok = run_tool(ctx, block.name, block.input)
                actions.append({"tool": block.name, "input": block.input, "result": result, "ok": ok})
                _log(call["id"], "tool", f"{block.name}({json.dumps(block.input)}) -> {json.dumps(result)}")
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(result), "is_error": not ok})
            messages.append({"role": "user", "content": results})
            if ctx["ended"]:
                break
        else:
            spoken.append("Let me have someone from the team call you back to sort this out.")
    except LLM_ERRORS:
        log.exception("Model API error on call %s", external_id)
        spoken = [FALLBACK_LINE]
        # History may now end on an unanswered user turn; close it so the next turn stays valid.
        if messages and messages[-1]["role"] == "user":
            messages.append({"role": "assistant", "content": FALLBACK_LINE})

    say = " ".join(spoken).strip() or "Sorry, could you say that again?"
    _log(call["id"], "agent", say)
    with get_db() as db:
        db.execute("UPDATE calls SET messages_json=? WHERE id=?", (json.dumps(messages), call["id"]))
    return {"say": say, "ended": ctx["ended"], "actions": actions}


def hang_up(external_id: str) -> None:
    """Caller hung up before end_call. Keep whatever was captured; mark it for follow-up."""
    with get_db() as db:
        db.execute("UPDATE calls SET status='completed', ended_at=?, outcome=coalesce(outcome,'caller_hung_up') "
                   "WHERE external_id=? AND status='active'", (now_iso(), external_id))
