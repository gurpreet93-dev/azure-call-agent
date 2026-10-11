import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # reads .env from the working directory; real environment variables win


@dataclass(frozen=True)
class Settings:
    # The agent (instructions + knowledge) lives in Microsoft Foundry; the app only references it.
    # Project endpoint: Foundry portal -> your project -> Home -> "Project endpoint".
    foundry_project_endpoint: str = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "").strip().rstrip("/")
    foundry_agent_name: str = os.getenv("FOUNDRY_AGENT_NAME", "").strip()
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


settings = Settings()
