"""One-command Microsoft Foundry check. Run:  python -m scripts.check_foundry

1. reads LLM_PROVIDER, FOUNDRY_RESOURCE, FOUNDRY_API_KEY and AGENT_MODEL from .env
2. sends one short message to your deployment (GPT or Claude)
3. sends it a tool, to prove the agent's actions (book a visit, send a proposal...) will work
"""
import sys

import anthropic
import openai

from app import agent
from app.config import settings

TOOL = {
    "name": "check_availability",
    "description": "List open inspection slots for a given day.",
    "input_schema": {"type": "object", "properties": {"day": {"type": "string"}}, "required": ["day"]},
}


def step(n: int, text: str) -> None:
    print(f"\n[{n}/3] {text}", flush=True)


def fail(exc: Exception) -> None:
    from app.llm_openai import openai_base_url
    host = (openai_base_url(settings.foundry_resource, settings.azure_openai_endpoint)
            if settings.llm_provider == "azure-openai" else f"https://{settings.foundry_resource}.services.ai.azure.com")
    if isinstance(exc, (anthropic.AuthenticationError, openai.AuthenticationError)):
        hint = "The key was rejected. Copy it again from the Foundry portal (project Overview), no spaces."
    elif isinstance(exc, (anthropic.NotFoundError, openai.NotFoundError)):
        hint = (f"No deployment called '{settings.agent_model}'. In the Foundry portal open Models + endpoints, "
                "copy the exact deployment name, and put it after AGENT_MODEL= in .env.")
    elif isinstance(exc, (anthropic.PermissionDeniedError, openai.PermissionDeniedError)):
        hint = "Your account can't use this deployment. Check your role on the Foundry resource (Azure AI User or higher)."
    elif isinstance(exc, (anthropic.RateLimitError, openai.RateLimitError)):
        hint = "Quota or rate limit hit. Wait a minute, or raise the deployment's rate limit in the portal."
    elif isinstance(exc, (anthropic.APIConnectionError, openai.APIConnectionError)):
        hint = (f"Couldn't reach {host}. Check FOUNDRY_RESOURCE is just the name (no https://), "
                "or paste the exact endpoint from the Foundry Home page into AZURE_OPENAI_ENDPOINT.")
    else:
        hint = "Unexpected error; paste this message to Claude."
    sys.exit(f"  ✗ {type(exc).__name__}: {exc}\n    {hint}")


def main() -> None:
    from app.llm_openai import openai_base_url
    step(1, "Reading Foundry settings from .env")
    if settings.llm_provider not in ("azure-openai", "foundry"):
        sys.exit(f"  ✗ LLM_PROVIDER is '{settings.llm_provider}'. Set LLM_PROVIDER=azure-openai (GPT) "
                 "or foundry (Claude) in .env.")
    where = settings.foundry_resource or (settings.llm_provider == "azure-openai" and settings.azure_openai_endpoint)
    missing = [n for n, v in (("FOUNDRY_RESOURCE", where),
                              ("FOUNDRY_API_KEY", settings.foundry_api_key)) if not v]
    if missing:
        sys.exit(f"  ✗ {', '.join(missing)} empty in .env. Both are on your project's Overview page in the Foundry portal.")
    if settings.foundry_resource and ("://" in settings.foundry_resource or "." in settings.foundry_resource):
        sys.exit("  ✗ FOUNDRY_RESOURCE should be only the name, e.g. 'my-resource', "
                 "not the full https://my-resource.services.ai.azure.com URL.")
    if settings.llm_provider == "azure-openai":
        print(f"  ✓ endpoint {openai_base_url(settings.foundry_resource, settings.azure_openai_endpoint)}"
              f" ({'from AZURE_OPENAI_ENDPOINT' if settings.azure_openai_endpoint else 'built from FOUNDRY_RESOURCE'})")
    print(f"  ✓ {settings.llm_provider}: resource '{settings.foundry_resource}', deployment '{settings.agent_model}', "
          f"key starts with {settings.foundry_api_key[:4]}…")

    step(2, "Sending one message to your deployment")
    try:
        resp = agent.client().messages.create(
            model=settings.agent_model, max_tokens=1024, **agent.model_options(),
            messages=[{"role": "user", "content": "Reply with exactly: Foundry connection OK"}])
    except agent.LLM_ERRORS as exc:
        fail(exc)
    print(f"  ✓ reply: {''.join(b.text for b in resp.content if b.type == 'text').strip()!r}")

    step(3, "Checking tool use (how the agent takes actions)")
    try:
        resp = agent.client().messages.create(
            model=settings.agent_model, max_tokens=1024, tools=[TOOL], **agent.model_options(),
            messages=[{"role": "user", "content": "Use the tool: what inspection slots are open on Monday?"}])
    except agent.LLM_ERRORS as exc:
        fail(exc)
    calls = [b for b in resp.content if b.type == "tool_use"]
    if not calls:
        sys.exit("  ✗ The model answered without calling the tool. The agent's actions won't run; tell Claude.")
    print(f"  ✓ model called {calls[0].name}({calls[0].input})")
    print("\nAll good: the app can run its agent on Foundry. Next: python -m app.seed, then uvicorn app.main:app")


if __name__ == "__main__":
    main()
