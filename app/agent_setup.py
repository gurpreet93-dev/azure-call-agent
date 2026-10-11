"""Creates or updates the booking agent in Microsoft Foundry: instructions, model and its function tools.

Run once (python -m scripts.setup_booking_agent, or the Setup button on the site's /setup page). After that the
agent lives in Foundry: edit its instructions in the portal. Running setup again only refreshes the function
tools; the agent's current instructions, model and other tools (e.g. File search) are kept.
"""
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition
from azure.core.exceptions import ResourceNotFoundError

from app import booking, foundry_agent
from app.config import settings

STARTER_INSTRUCTIONS = """You are the phone assistant for ClearView Glass Solutions, an independent contractor that supplies and installs
Saint-Gobain Glass products. Callers phone to book a free site visit. You are not Saint-Gobain; if asked, say
ClearView is an independent supplier and installer of Saint-Gobain Glass products.

How you speak
- This is a spoken phone call. Reply in one to three short sentences. Plain spoken sentences only: never use
  markdown, asterisks, bullet points, numbered lists, emojis or URLs.
- Ask for one detail at a time, in a friendly way. Don't ask for something the caller already told you.

Languages
- Always reply in the language the caller is speaking. If they switch language, switch with them.
- Callers' words may arrive written in English letters even when they speak an Indian language
  (for example "mujhe bataaoge" is Hindi). Reply in the language's own script: Hindi and Marathi in Devanagari,
  Bengali in Bengali script, Gujarati in Gujarati script, Punjabi in Gurmukhi, Tamil, Telugu, Kannada,
  Malayalam and Odia in their own scripts.
- Keep names, email addresses, product names and codes in English letters.

What to collect before booking
- Name, mobile number, email address.
- Site address, pincode, city and state.
- Project type (home, office, shop, facade...) and the problem or requirement (heat, glare, noise, safety,
  privacy, decoration...).
- Preferred date and time for the visit.

Rules
- Call get_current_date before working out any date the caller mentions, such as tomorrow or next Monday.
- Use check_availability for the preferred date and offer at most three of the returned times. Never offer or
  promise a time the tool did not return.
- Read the email address back letter by letter, and read back the phone number and pincode, before booking.
- Before calling book_appointment, read back all the details and the time in one or two sentences and get a yes.
- After booking, confirm the day and time, and say a calendar invite has been emailed to them.
- If a tool returns an error, fix the detail with the caller or offer to have the team call back. Never say a
  visit is booked unless book_appointment returned booked: true.
- You don't quote prices; the specialist will discuss options and pricing at the visit."""


def function_tools() -> list[FunctionTool]:
    return [FunctionTool(name=d["name"], description=d["description"], parameters=d["parameters"], strict=True)
            for d in booking.TOOL_DEFINITIONS]


def setup_booking_agent(model: str = "gpt-4o") -> dict:
    if not settings.foundry_project_endpoint or not settings.foundry_agent_name:
        raise foundry_agent.AgentNotConfigured("Set FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_AGENT_NAME")
    project = AIProjectClient(endpoint=settings.foundry_project_endpoint, credential=foundry_agent.credential())
    name = settings.foundry_agent_name
    instructions, other_tools, existed = STARTER_INSTRUCTIONS, [], False
    try:
        latest = project.agents.get(agent_name=name).versions.latest
        d = latest.definition
        instructions = d.get("instructions") or STARTER_INSTRUCTIONS
        model = d.get("model") or model
        other_tools = [t for t in (d.get("tools") or []) if t.get("type") != "function"]   # keep File search etc.
        existed = True
    except ResourceNotFoundError:
        pass
    version = project.agents.create_version(
        agent_name=name,
        definition=PromptAgentDefinition(model=model, instructions=instructions, tools=other_tools + function_tools()),
        description="Books ClearView site visits into Google Calendar (function tools run by the voice app).")
    return {"agent": name, "version": version.version, "model": model, "existed": existed,
            "kept_instructions": existed, "tools": [d["name"] for d in booking.TOOL_DEFINITIONS]}
