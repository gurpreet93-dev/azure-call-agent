"""One-command check of everything the voice screen needs. Run:  python -m scripts.check_agent

1. settings in .env
2. Microsoft Entra sign-in (az login) for the Foundry project
3. a short conversation with your Foundry agent
4. Azure Speech turning a sentence into audio
"""
import sys

import openai

from app import foundry_agent, speech
from app.config import settings

ROLE_HINT = ("Give yourself the 'Azure AI User' role: Azure portal -> your Foundry resource -> Access control (IAM) "
             "-> Add role assignment -> Azure AI User -> your account. Wait ~5 minutes, then retry.")


def step(n: int, text: str) -> None:
    print(f"\n[{n}/4] {text}", flush=True)


def main() -> None:
    step(1, "Reading settings from .env")
    missing = [n for n, v in (("FOUNDRY_PROJECT_ENDPOINT", settings.foundry_project_endpoint),
                              ("FOUNDRY_AGENT_NAME", settings.foundry_agent_name)) if not v]
    if missing:
        sys.exit(f"  ✗ {', '.join(missing)} empty in .env. The project endpoint is on the Foundry Home page; "
                 "the agent name is the one you gave it under Build -> Agents.")
    if "/api/projects/" not in settings.foundry_project_endpoint:
        sys.exit("  ✗ FOUNDRY_PROJECT_ENDPOINT should be the *Project endpoint* "
                 "(https://<resource>.services.ai.azure.com/api/projects/<project>), not the Azure OpenAI one.")
    print(f"  ✓ project {settings.foundry_project_endpoint}")
    print(f"  ✓ agent '{settings.foundry_agent_name}', voice {settings.speech_voice} ({settings.speech_region})")

    step(2, "Signing in to Microsoft Entra ID (a browser window may open: sign in with your Azure account)")
    try:
        foundry_agent.credential().get_token("https://ai.azure.com/.default")
    except Exception as exc:
        sys.exit(f"  ✗ Sign-in failed: {str(exc).splitlines()[0]}\n    Sign in with the account that owns the Azure "
                 "subscription. No browser here (e.g. Codespaces)? Run  az login --use-device-code  first.")
    print("  ✓ signed in")

    step(3, "Talking to your Foundry agent")
    try:
        conv = foundry_agent.new_conversation()
        reply = foundry_agent.ask(conv, "This is a connection test. Reply with one short friendly sentence.")
    except (openai.PermissionDeniedError, openai.AuthenticationError) as exc:
        sys.exit(f"  ✗ Foundry refused access: {exc}\n    {ROLE_HINT}")
    except openai.NotFoundError as exc:
        sys.exit(f"  ✗ Not found: {exc}\n    Check FOUNDRY_AGENT_NAME matches the agent's name in Foundry exactly "
                 "(Build -> Agents), and that it has been saved.")
    except openai.APIConnectionError as exc:
        sys.exit(f"  ✗ Couldn't reach {settings.foundry_project_endpoint}: {exc}")
    except openai.APIError as exc:
        sys.exit(f"  ✗ {type(exc).__name__}: {exc}")
    if not reply:
        sys.exit("  ✗ The agent answered with no text. Open it in Foundry and check it has a model and instructions.")
    print(f"  ✓ agent replied: {reply!r}")

    step(4, "Speaking a sentence with Azure Speech")
    if not settings.speech_key:
        sys.exit("  ✗ No speech key. Set SPEECH_KEY (or FOUNDRY_API_KEY), the key on the Foundry Home page.")
    try:
        audio = speech.synthesize("नमस्ते, यह एक परीक्षण है।", "hi-IN")  # Hindi line: exercises the native Hindi voice
    except speech.SpeechError as exc:
        hint = ("Key rejected: use the API key from the Foundry Home page." if " 401" in str(exc) else
                f"Check SPEECH_REGION ('{settings.speech_region}') is your resource's region and "
                f"SPEECH_VOICE ('{settings.speech_voice}') is a valid voice name.")
        sys.exit(f"  ✗ {exc}\n    {hint}")
    except Exception as exc:
        sys.exit(f"  ✗ Couldn't reach Azure Speech: {exc}")
    print(f"  ✓ got {len(audio):,} bytes of audio (Hindi test sentence, voice {speech.voice_for('hi-IN')})")
    print("\nAll good. Start the screen:  uvicorn app.main:app")


if __name__ == "__main__":
    main()
