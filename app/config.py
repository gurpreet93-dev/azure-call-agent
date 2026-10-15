import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # reads .env from the working directory; real environment variables win


# The Google Sheet's Apps Script web app ("ClearView Leads (demo)"). Empty = lead capture off.
LEAD_SHEET_URL = "https://script.google.com/macros/s/AKfycbzbMrFpA4rn_lXv8C-X5VsCo2T_jGDEaOt7R7zoM7ZZxsw6GHvJqXVpWBrDeQCaZ3c/exec"


@dataclass(frozen=True)
class Settings:
    # The agent (instructions + knowledge) lives in Microsoft Foundry; the app only references it.
    # Project endpoint: Foundry portal -> your project -> Home -> "Project endpoint".
    foundry_project_endpoint: str = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "").strip().rstrip("/")
    foundry_agent_name: str = os.getenv("FOUNDRY_AGENT_NAME", "").strip()
    # Other Foundry agents the page may pick (comma-separated), e.g. the same agent on Foundry's File search.
    other_agents: str = os.getenv("OTHER_AGENTS", "clearview-glass-agent-filesearch")
    # Cap on each reply's length. Spoken replies are 1-3 sentences; the cap also keeps each request's
    # tokens-per-minute charge small.
    agent_max_output_tokens: int = int(os.getenv("AGENT_MAX_OUTPUT_TOKENS", "400"))

    # Azure Speech (part of the same Foundry resource) speaks the agent's replies.
    # The Foundry resource's API key works here; SPEECH_KEY falls back to FOUNDRY_API_KEY.
    speech_key: str = (os.getenv("SPEECH_KEY") or os.getenv("FOUNDRY_API_KEY") or "").strip()
    speech_region: str = os.getenv("SPEECH_REGION", "eastus2").strip()
    # Multilingual neural voices speak any supported language with one voice.
    speech_voice: str = os.getenv("SPEECH_VOICE", "en-US-AvaMultilingualNeural").strip()
    # Optional per-language voice overrides, e.g. "hi-IN=hi-IN-MadhurNeural,pa-IN=pa-IN-OjasNeural"
    speech_voices: str = os.getenv("SPEECH_VOICES", "")

    # Set on a public website so only people you give it to can use (and spend on) the agent.
    # The browser shows its own sign-in box; any username, this password.
    app_password: str = os.getenv("APP_PASSWORD", "")
    # On a server there is no browser for an interactive sign-in. Azure App Service sets WEBSITE_SITE_NAME
    # (sign-in: managed identity); Vercel sets VERCEL (sign-in: service principal via AZURE_TENANT_ID,
    # AZURE_CLIENT_ID, AZURE_CLIENT_SECRET).
    on_server: bool = bool(os.getenv("WEBSITE_SITE_NAME") or os.getenv("VERCEL"))

    # Lead capture: when a call ends, a row goes to a Google Sheet through an Apps Script web app bound to it.
    # The script URL is public in this repo, so the script also checks a token: the site password by default,
    # which lives only in the server's environment.
    lead_sheet_url: str = os.getenv("LEAD_SHEET_URL", LEAD_SHEET_URL).strip()
    lead_sheet_token: str = (os.getenv("LEAD_SHEET_TOKEN") or os.getenv("APP_PASSWORD") or "").strip()
    # Model deployment that reads the finished call and returns the lead's fields.
    lead_model: str = os.getenv("LEAD_MODEL", "gpt-4.1").strip()


settings = Settings()
