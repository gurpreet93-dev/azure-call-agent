"""Talks to an agent defined in Microsoft Foundry (Foundry Agent Service, Responses API).

Everything the agent knows and how it behaves (instructions, knowledge files, tools, model) is
configured in Foundry. This module sends the caller's words to the agent by name, with the conversation so far
as plain text (see `ask`). Sign-in is Microsoft Entra ID: an existing `az login` if there is one, otherwise a
browser window opens once to sign in (so nothing else needs installing on a local machine).
"""
from dataclasses import dataclass
from functools import lru_cache

import openai
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from app.config import settings


AGENT_TIMEOUT_S = 45


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


def _agent_ref() -> dict:
    return {"agent_reference": {"name": settings.foundry_agent_name, "type": "agent_reference"}}


def new_conversation() -> str:
    """Only for the older "stored" mode: Foundry keeps the history server-side and the browser holds its id."""
    return _openai_client().conversations.create().id


# Azure counts each request against the tokens-per-minute limit as its input PLUS the most it may write, so an
# uncapped request "costs" thousands of tokens even for a one-sentence reply. Spoken replies are short.
LIMITS = {"max_output_tokens": settings.agent_max_output_tokens, "max_tool_calls": 2, "truncation": "auto"}
_limits_supported = True


@dataclass
class Answer:
    text: str
    input_tokens: int | None = None
    cached_tokens: int | None = None


def _answer(response) -> Answer:
    usage = getattr(response, "usage", None)
    details = getattr(usage, "input_tokens_details", None)
    return Answer((response.output_text or "").strip(), getattr(usage, "input_tokens", None),
                  getattr(details, "cached_tokens", None))


def ask(conversation_id: str | None, text: str, history: list[dict] | None = None) -> Answer:
    """With `history` (the earlier turns as plain text, kept by the browser) the agent gets only what was said:
    no stored conversation, so earlier search results are not sent again. A Foundry conversation re-sends every
    earlier tool call with its full search output on each turn (2,745 input tokens on turn 1, 14,281 by turn 5).
    The agent searches again when it needs a figure. Without `history`, the stored conversation is used."""
    global _limits_supported
    if history is not None:
        target = {"input": [{"role": m["role"], "content": m["content"]} for m in history] +
                           [{"role": "user", "content": text}]}
    else:
        target = {"conversation": conversation_id, "input": text}
    client = _openai_client()
    if _limits_supported:
        try:
            return _answer(client.responses.create(**target, extra_body=_agent_ref(), **LIMITS))
        except openai.BadRequestError as exc:      # the agent service refused one of the limits: go without them
            if not any(k in str(exc) for k in LIMITS):
                raise
            _limits_supported = False
    return _answer(client.responses.create(**target, extra_body=_agent_ref()))
