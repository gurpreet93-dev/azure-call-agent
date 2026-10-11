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

    # Azure Speech (part of the same Foundry resource) speaks the agent's replies.
    # The Foundry resource's API key works here; SPEECH_KEY falls back to FOUNDRY_API_KEY.
    speech_key: str = (os.getenv("SPEECH_KEY") or os.getenv("FOUNDRY_API_KEY") or "").strip()
    speech_region: str = os.getenv("SPEECH_REGION", "eastus2").strip()
    # Multilingual neural voices speak any supported language with one voice.
    speech_voice: str = os.getenv("SPEECH_VOICE", "en-US-AvaMultilingualNeural").strip()


settings = Settings()
