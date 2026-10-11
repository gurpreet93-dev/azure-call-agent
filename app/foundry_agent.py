"""Talks to an agent defined in Microsoft Foundry (Foundry Agent Service, Responses API).

Everything the agent knows and how it behaves (instructions, knowledge files, tools, model) is
configured in Foundry. This module only creates a conversation and sends the user's words to the
agent by name. Sign-in is Microsoft Entra ID: an existing `az login` if there is one, otherwise a
browser window opens once to sign in (so nothing else needs installing on a local machine).
"""
from functools import lru_cache

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from app.config import settings


class AgentNotConfigured(RuntimeError):
    pass


@lru_cache(maxsize=1)
def credential() -> DefaultAzureCredential:
    # Locally a browser window can open to sign in; on App Service the managed identity is used instead
    # (a server has no browser, so the pop-up option would only stall requests there).
    return DefaultAzureCredential(exclude_interactive_browser_credential=settings.on_app_service)


@lru_cache(maxsize=1)
def _openai_client():
    if not settings.foundry_project_endpoint or not settings.foundry_agent_name:
        raise AgentNotConfigured("Set FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_AGENT_NAME in .env")
    project = AIProjectClient(endpoint=settings.foundry_project_endpoint, credential=credential())
    return project.get_openai_client()


def _agent_ref() -> dict:
    return {"agent_reference": {"name": settings.foundry_agent_name, "type": "agent_reference"}}


def new_conversation() -> str:
    """Foundry keeps the conversation history server-side; the browser only holds its id."""
    return _openai_client().conversations.create().id


def ask(conversation_id: str, text: str) -> str:
    response = _openai_client().responses.create(conversation=conversation_id, input=text, extra_body=_agent_ref())
    return (response.output_text or "").strip()
