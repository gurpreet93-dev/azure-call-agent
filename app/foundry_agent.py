"""Talks to an agent defined in Microsoft Foundry (Foundry Agent Service, Responses API).

Everything the agent knows and how it behaves (instructions, knowledge files, tools, model) is
configured in Foundry. This module only creates a conversation and sends the user's words to the
agent by name. Sign-in is Microsoft Entra ID: an existing `az login` if there is one, otherwise a
browser window opens once to sign in (so nothing else needs installing on a local machine).
"""
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
    """Foundry keeps the conversation history server-side; the browser only holds its id."""
    return _openai_client().conversations.create().id


# Azure counts each request against the tokens-per-minute limit as its input PLUS the most it may write, so an
# uncapped request "costs" thousands of tokens even for a one-sentence reply. Spoken replies are short.
LIMITS = {"max_output_tokens": settings.agent_max_output_tokens, "max_tool_calls": 2, "truncation": "auto"}
_limits_supported = True


def ask(conversation_id: str, text: str) -> str:
    global _limits_supported
    client = _openai_client()
    if _limits_supported:
        try:
            response = client.responses.create(conversation=conversation_id, input=text, extra_body=_agent_ref(),
                                               **LIMITS)
            return (response.output_text or "").strip()
        except openai.BadRequestError as exc:      # the agent service refused one of the limits: go without them
            if not any(k in str(exc) for k in LIMITS):
                raise
            _limits_supported = False
    response = client.responses.create(conversation=conversation_id, input=text, extra_body=_agent_ref())
    return (response.output_text or "").strip()
