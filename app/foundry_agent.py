"""Talks to an agent defined in Microsoft Foundry (Foundry Agent Service, Responses API).

Everything the agent knows and how it behaves (instructions, knowledge files, tools, model) is
configured in Foundry. This module sends the caller's words to the agent by name, with the conversation so far
as plain text (see `ask`). Sign-in is Microsoft Entra ID: an existing `az login` if there is one, otherwise a
browser window opens once to sign in (so nothing else needs installing on a local machine).
"""
import logging
import time
from dataclasses import dataclass
from functools import lru_cache

import openai
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from app.config import settings


AGENT_TIMEOUT_S = 45
log = logging.getLogger(__name__)


class AgentNotConfigured(RuntimeError):
    pass


@lru_cache(maxsize=1)
def credential() -> DefaultAzureCredential:
    # Locally a browser window can open to sign in. On a server (App Service, Vercel) the pop-up option is off,
    # since there is no browser and it would only stall requests; the managed identity or the service
    # principal in AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET is used instead.
    return DefaultAzureCredential(exclude_interactive_browser_credential=settings.on_server)


@lru_cache(maxsize=1)
def _openai_client():
    if not settings.foundry_project_endpoint or not settings.foundry_agent_name:
        raise AgentNotConfigured("Set FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_AGENT_NAME in .env")
    project = AIProjectClient(endpoint=settings.foundry_project_endpoint, credential=credential())
    # Fail fast on a phone call: the OpenAI library would otherwise wait out a rate limit and retry (up to about
    # two minutes of silence). One attempt, a hard time limit, and the caller hears a clear message instead.
    return project.get_openai_client().with_options(timeout=AGENT_TIMEOUT_S, max_retries=0)


def allowed_agents() -> set[str]:
    return {settings.foundry_agent_name, *(a.strip() for a in settings.other_agents.split(",") if a.strip())}


def _agent_ref(agent: str | None = None) -> dict:
    return {"agent_reference": {"name": agent or settings.foundry_agent_name, "type": "agent_reference"}}


def new_conversation() -> str:
    """Only for the older "stored" mode: Foundry keeps the history server-side and the browser holds its id."""
    client = _openai_client()
    try:
        return client.conversations.create().id
    except openai.APIStatusError as exc:           # Foundry times out now and then (408): one more try
        if exc.status_code != 408:
            raise
        log.warning("Foundry conversation create timed out (408), retrying once")
        return client.conversations.create().id


# Azure counts each request against the tokens-per-minute limit as its input PLUS the most it may write, so an
# uncapped request "costs" thousands of tokens even for a one-sentence reply. Spoken replies are short.
LIMITS = {"max_output_tokens": settings.agent_max_output_tokens, "max_tool_calls": 2, "truncation": "auto"}
_limits_supported = True


# Plain-text history shows earlier answers without the searches behind them, and the model copies that: it stopped
# searching and answered "no confirmed information" (seen in traces). The reminder goes with every turn, after the
# caller's message: placed before it, gpt-4.1 still skipped searches once old results were deleted. Foundry's own
# File search note sits after the message, and that agent searched on every turn.
# Worded for product questions only: as a blanket "search before any product fact" it made the agent search on
# lead-capture turns (after a pincode it listed window glass types instead of confirming the details).
SEARCH_REMINDER = ("If the caller's message above asks about a product, search the knowledge base before stating "
                   "any product fact, even if an earlier answer in this conversation covers it; earlier answers are "
                   "not search results. Otherwise don't search: reply once, briefly, and carry on collecting the "
                   "caller's details. When the caller asks you to repeat, repeat your last answer exactly.")


@dataclass
class Answer:
    text: str
    input_tokens: int | None = None
    cached_tokens: int | None = None
    searched: bool | None = None          # did this turn call a tool (a search)? None = unknown
    message_ids: tuple[str, ...] = ()     # the reply's items, to take a blocked reply out of the conversation
    extra_ids: tuple[str, ...] = ()       # further reply messages in the same turn, dropped (see _answer)


def _message_text(item) -> str:
    return "".join(getattr(p, "text", "") or "" for p in (getattr(item, "content", None) or [])).strip()


def _answer(response) -> Answer:
    """The turn's reply. On lead-capture turns the agent sometimes returned the same question two to four times
    as separate messages, or ran ahead ("Is that correct?" then "What's your address?"). Only the first message
    is the reply; the others are returned in extra_ids so they can be deleted from the stored conversation."""
    usage = getattr(response, "usage", None)
    details = getattr(usage, "input_tokens_details", None)
    output = getattr(response, "output", None)
    searched = None if output is None else any(getattr(i, "type", "message") != "message" for i in output)
    messages = [i for i in (output or []) if getattr(i, "type", "") == "message" and _message_text(i)]
    if len(messages) > 1:
        first = messages[0]
        return Answer(_message_text(first), getattr(usage, "input_tokens", None),
                      getattr(details, "cached_tokens", None), searched,
                      tuple(i for i in [getattr(first, "id", None)] if i),
                      tuple(m.id for m in messages[1:] if getattr(m, "id", None)))
    ids = tuple(i.id for i in (output or []) if getattr(i, "type", "") == "message" and getattr(i, "id", None))
    return Answer((response.output_text or "").strip(), getattr(usage, "input_tokens", None),
                  getattr(details, "cached_tokens", None), searched, ids)


def _message(role: str, content: str) -> dict:
    return {"type": "message", "role": role, "content": content}


_system_note_supported = True


def _text_history_input(history: list[dict], text: str) -> list[dict]:
    earlier = [_message(m["role"], m["content"]) for m in history]
    if _system_note_supported:
        return earlier + [_message("user", text), _message("system", SEARCH_REMINDER)]
    # The plain shape that the agent service accepted before the system note was added.
    return [{"role": m["role"], "content": m["content"]} for m in history] + \
           [{"role": "user", "content": f"{text}\n\n({SEARCH_REMINDER})"}]


TOOL_RETRY_WITHIN_S = 20
RETRY_STATUS = {408, 424}   # Foundry's own timeout, and its search tool's intermittent tool_server_error


def _send(client, **kw):
    """One retry when Foundry's search tool fails with 424 tool_server_error, which is intermittent (the search
    service itself answered in ~55 ms). Only when the failure came quickly, so a retry still fits the time limit."""
    started = time.monotonic()
    try:
        return client.responses.create(**kw)
    except openai.APIStatusError as exc:
        if exc.status_code not in RETRY_STATUS or time.monotonic() - started > TOOL_RETRY_WITHIN_S:
            raise
        log.warning("Foundry failed (%s), retrying once: %s", exc.status_code, exc)
        return client.responses.create(**kw)


def _create(client, target: dict, agent: str | None = None):
    global _limits_supported
    if _limits_supported:
        try:
            return _send(client, **target, extra_body=_agent_ref(agent), **LIMITS)
        except openai.BadRequestError as exc:      # the agent service refused one of the limits: go without them
            if not any(k in str(exc) for k in LIMITS):
                raise
            _limits_supported = False
    return _send(client, **target, extra_body=_agent_ref(agent))


def ask(conversation_id: str | None, text: str, history: list[dict] | None = None,
        agent: str | None = None) -> Answer:
    """With `history` (the earlier turns as plain text, kept by the browser) the agent gets only what was said:
    no stored conversation, so earlier search results are not sent again. A Foundry conversation re-sends every
    earlier tool call with its full search output on each turn (2,745 input tokens on turn 1, 14,281 by turn 5).
    The agent searches again when it needs a figure. Without `history`, the stored conversation is used."""
    global _system_note_supported
    client = _openai_client()
    # The search reminder goes with every turn in both modes: without it gpt-4.1 answered "Nano Silver U-value 1.0"
    # (it is 1.6) without searching. In a stored conversation trim_search_results() deletes it after the turn.
    target = (lambda: {"conversation": conversation_id, "input": _text_history_input([], text)}) if history is None \
        else (lambda: {"input": _text_history_input(history, text)})
    try:
        return _answer(_create(client, target(), agent))
    except openai.BadRequestError as exc:
        if history is None and HALF_PAIR in str(exc):  # a clean-up left half a search pair: repair, then ask again
            if not repair_conversation(conversation_id):  # pairs not recognisable: clear every search item instead
                trim_search_results(conversation_id)
            return _answer(_create(client, target(), agent))
        if not _system_note_supported or "input" not in str(exc):   # the system note refused: put it in the turn
            raise
        _system_note_supported = False
        return _answer(_create(client, target(), agent))


FORCE_SEARCH_NOTE = ("Search the knowledge base now and answer the caller's last question from the results only.")
_force_supported = True


def ask_searching(conversation_id: str | None, text: str, history: list[dict] | None = None,
                  agent: str | None = None) -> Answer | None:
    """Asks again with a search required (tool_choice), after a reply stated a figure without searching.

    In a stored conversation the caller's question is already there, so only a note is sent. Returns None if the
    agent service doesn't accept a required tool call (the caller then gets the "let me check" reply)."""
    global _force_supported
    if not _force_supported:
        return None
    note = _message("system", FORCE_SEARCH_NOTE)
    target = {"conversation": conversation_id, "input": [note]} if history is None else \
        {"input": [_message(m["role"], m["content"]) for m in history] + [note, _message("user", text)]}
    try:
        return _answer(_create(_openai_client(), {**target, "tool_choice": "required"}, agent))
    except openai.BadRequestError as exc:
        log.warning("Required search refused by the agent service: %s", exc)
        _force_supported = False
        return None


TRIM_CALL_TIMEOUT_S = 6
TRIM_BUDGET_S = 7
# A search is stored as a pair: the call and its output, linked by call_id. Foundry refuses to go on with a
# conversation that holds one half ("No tool output found for remote function call ..."), which happened when a
# clean-up deleted the output and ran out of time before the call. So pairs are deleted together, and any half
# left behind is repaired before the next turn.
HALF_PAIR = "No tool output found"
PAIRED_CALLS = {"function_call", "remote_function_call", "custom_tool_call", "mcp_call"}


def _is_output(item) -> bool:
    return getattr(item, "type", "").endswith("_output")


def _droppable(item) -> bool:
    """Search calls, their results and the per-turn reminder; never the caller's words or the agent's answers."""
    return getattr(item, "type", "message") != "message" or getattr(item, "role", None) == "system"


def _groups(items) -> list[list]:
    """Droppable items, each search call together with its output, outputs first."""
    groups: dict[str, list] = {}
    for item in items:
        if _droppable(item):
            groups.setdefault(getattr(item, "call_id", None) or item.id, []).append(item)
    return [sorted(g, key=lambda i: not _is_output(i)) for g in groups.values()]


def _halves(items) -> list:
    """Call or output items whose partner is missing (left by an interrupted clean-up)."""
    calls, outputs = {}, {}
    for item in items:
        cid = getattr(item, "call_id", None)
        if not cid:
            continue
        if _is_output(item):
            outputs[cid] = item
        elif getattr(item, "type", "") in PAIRED_CALLS:
            calls[cid] = item
    return [calls[c] for c in calls.keys() - outputs.keys()] + [outputs[c] for c in outputs.keys() - calls.keys()]


def _delete(client, conversation_id: str, item_id: str) -> None:
    for attempt in range(3):     # one at a time: Foundry refuses simultaneous changes (412 "modified concurrently")
        try:
            client.conversations.items.delete(item_id, conversation_id=conversation_id)
            return
        except openai.APIStatusError as exc:
            if exc.status_code == 404:                   # already gone
                return
            if exc.status_code != 412 or attempt == 2:
                raise
            time.sleep(0.2 * (attempt + 1))


def _client_for_trim():
    return _openai_client().with_options(timeout=TRIM_CALL_TIMEOUT_S, max_retries=0)


def repair_conversation(conversation_id: str) -> int:
    """Deletes half-deleted search pairs, so the conversation can go on. Returns how many items were removed."""
    client = _client_for_trim()
    halves = _halves(client.conversations.items.list(conversation_id, limit=100, order="desc"))
    for item in halves:
        _delete(client, conversation_id, item.id)
    if halves:
        log.warning("repaired %s: removed %d half-deleted search item(s)", conversation_id, len(halves))
    return len(halves)


def trim_search_results(conversation_id: str) -> dict:
    """Deletes the search calls and their results from a stored conversation, keeping the questions and answers.

    Foundry re-sends every item of a conversation on each turn, so search results from earlier turns made each
    question cost about 2,900 more input tokens than the one before. Each turn's trace still records the results
    it used; only the copy in the conversation goes. Runs while the reply plays, so the caller doesn't wait."""
    started = time.perf_counter()
    # The next question waits for this, and Foundry's deletes once took 30 s: each call gets a short limit, and
    # no new pair is started after TRIM_BUDGET_S (the next turn then carries a few extra tokens, nothing worse).
    client = _client_for_trim()
    items = list(client.conversations.items.list(conversation_id, limit=100, order="desc"))
    groups = _groups(items)
    doomed = sum(len(g) for g in groups)
    removed, repaired = [], 0
    for item in _halves(items):                          # first finish anything an earlier clean-up left half done
        _delete(client, conversation_id, item.id)
        removed.append(item)
        repaired += 1
    done = {i.id for i in removed}
    for group in groups:
        group = [i for i in group if i.id not in done]
        if not group:
            continue
        if time.perf_counter() - started > TRIM_BUDGET_S:
            log.warning("trim stopped after %.1fs with %d item(s) left", time.perf_counter() - started,
                        doomed - len(removed))
            break
        for item in group:                               # a pair goes whole; a timeout here leaves a half that
            _delete(client, conversation_id, item.id)    # repair_conversation() removes before the next turn
            removed.append(item)
    return {"removed": len(removed), "left": doomed - len(removed), "repaired": repaired,
            "kept": len(items) - doomed, "types": sorted({getattr(i, "type", "?") for i in removed}),
            "seconds": time.perf_counter() - started}


def remove_items(conversation_id: str, item_ids) -> None:
    """Deletes particular items (a blocked reply) from a stored conversation, so later turns don't build on it."""
    client = _client_for_trim()
    for item_id in item_ids:
        _delete(client, conversation_id, item_id)
