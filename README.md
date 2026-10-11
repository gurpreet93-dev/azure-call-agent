# Foundry Voice Agent

Talk to an AI agent in your own language. The browser turns your speech into text, an agent in
**Microsoft Foundry** decides what to say, and **Azure Speech** speaks the reply.

The agent's instructions, knowledge base and model are configured **in Foundry only**. This app
holds no prompt and no documents; it references the agent by name.

```
mic ─► browser speech recognition (language you pick) ─► text
     ─► this app ─► Foundry agent: instructions + knowledge files + model (all in Foundry)
     ─► reply text ─► Azure Speech, multilingual neural voice ─► audio in the browser
```

| Part | Where it lives |
|---|---|
| Speech → text | Browser (Chrome / Edge Web Speech API) |
| Instructions, knowledge, model, conversation history | Microsoft Foundry (Foundry Agent Service) |
| Text → speech | Azure Speech, same Foundry resource |
| The screen | `app/static/index.html` + `app/main.py` (FastAPI) |

The earlier full app (bookings, proposals, escalation, Twilio, Pinecone, evals) is on the
[`full-app`](../../tree/full-app) branch.

## 1. Set up the agent in Foundry (portal, ~10 minutes)

1. **Build → Agents → Create agent.** Name: `summit-receptionist`. Model: your `gpt-4o` deployment.
2. **Instructions** — paste:
   ```
   You are the phone receptionist for Summit Roofing, a residential roofing contractor.
   This is a spoken conversation: reply in one to three short sentences, no lists, no markdown, no emojis.
   Always reply in the same language the caller is speaking. If they switch language, switch with them.
   Answer only from the knowledge files. If the answer is not there, say you will have the team follow up.
   Quote prices only if they appear in the price list, and say they include 13% HST only if the list says so.
   If water is actively coming in or there is a safety risk, treat it as an emergency: say you are alerting
   the team and ask for their name, phone number and address. Never promise an arrival time.
   Collect the caller's name, phone number and address before ending, and confirm them back.
   ```
3. **Knowledge → Add → Files**: upload the three files in [`sample_kb/`](sample_kb) (company info,
   customer handbook, price list). Wait until indexing finishes.
4. **Save**, then try it in the agent playground.

## 2. Give yourself permission to call it

Subscription Owner manages resources but may not include *using* the agent.
Azure portal → your Foundry resource (`voice-agent-gurpreet`) → **Access control (IAM)** → **Add role
assignment** → **Azure AI User** → select your account → **Review + assign**. Allow ~5 minutes.

## 3. Run it (GitHub Codespaces, nothing to install)

1. GitHub → **Settings → Codespaces → Secrets**: `FOUNDRY_API_KEY` = the key from the Foundry Home page
   (used for Azure Speech).
2. This repo → **Code → Codespaces → Create codespace on main** (or **Rebuild Container** on an existing one,
   so the Azure CLI is installed).
3. In the codespace terminal:
   ```
   az login --use-device-code
   python -m scripts.check_agent
   uvicorn app.main:app
   ```
4. Open port 8000, pick a language, tap the microphone and speak.

`scripts/check_agent.py` checks each link in turn (settings, Entra sign-in, the agent, Azure Speech) and says
what to fix when one fails.

## Settings (`.env`)

| Setting | What it is |
|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | Foundry Home page → **Project endpoint** (`…services.ai.azure.com/api/projects/<project>`) |
| `FOUNDRY_AGENT_NAME` | The agent's name in Foundry |
| `SPEECH_KEY` | Foundry resource key; blank = use `FOUNDRY_API_KEY` |
| `SPEECH_REGION` | The resource's region (`eastus2`) |
| `SPEECH_VOICE` | Azure neural voice; the default multilingual voice speaks every listed language |

## Notes

- **Languages.** Browser speech recognition needs to be told the language, hence the picker. The agent
  replies in whatever language it hears; the voice is pinned to the picked language.
- **If Azure Speech fails**, the browser speaks the reply with its own voice and the reply bubble says so.
- **Conversation memory** is held by Foundry (one conversation per page session; **New conversation** resets it).
- **Not yet:** Foundry *voice agents* (realtime audio end to end) need a `gpt-realtime` deployment; this
  subscription has no quota for it yet.

## Tests

```
pytest -q
```
Foundry and Azure Speech are replaced with fakes, so the tests run without an Azure account.
