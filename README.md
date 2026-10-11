# Foundry Voice Agent

Phone-style voice assistant for a glass supplier, in the caller's own language. The browser turns your speech into text, an agent in
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

## The agent: ClearView Glass Solutions (fictional)

An independent contractor that **supplies and installs Saint-Gobain Glass products** for homes and
commercial buildings. Callers phone in to ask about the products; the agent answers from the Saint-Gobain
Glass brochures uploaded to Foundry. ClearView is fictional and is not Saint-Gobain or an authorised partner.

## 1. Set up the agent in Foundry (portal, ~10 minutes)

1. **Build → Agents → Create agent.** Name: `clearview-glass-agent`. Model: your `gpt-4o` deployment.
2. **Instructions** — paste:
   ```
   You are the phone assistant for ClearView Glass Solutions, an independent contractor that supplies and
   installs Saint-Gobain Glass products for homes, offices and commercial buildings. Callers ask you about
   these products. You are not Saint-Gobain and not an official Saint-Gobain representative; if asked, say
   ClearView is an independent supplier and installer of Saint-Gobain Glass products.

   How you speak
   - This is a spoken phone call. Reply in one to three short sentences. No lists, markdown, emojis or URLs.
   - Always reply in the language the caller is speaking. If they switch language, switch with them.
   - Ask one question at a time.

   What you know
   - Answer only from the Saint-Gobain Glass brochures in your knowledge files. Search them before answering
     any product question.
   - Give technical values (thickness, U-value, solar factor, light transmission, acoustic rating, fire rating,
     sizes) only exactly as they appear in the brochures. Never estimate, round or combine figures.
   - If the brochures don't cover something, say so and offer to have a ClearView specialist call back.
   - The brochures have no prices. Never quote a price; offer a quotation from the team instead.

   Helping the caller
   - Find out what they need: the project (home, office, facade, shower, partition...), the problem to solve
     (heat, glare, noise, safety, privacy, decoration) and the location. Suggest products that fit, naming them
     as the brochures do, and say briefly why.
   - When they want a quote or a site visit, collect their name, phone number, city and project details,
     and read them back to confirm.
   - For installation safety, building-code or certification questions you can't answer from the brochures,
     offer a specialist callback rather than guessing.
   ```
3. **Knowledge → Add → Files**: upload the Saint-Gobain Glass brochures (PDF). Wait until indexing finishes.
   Brochures that are scanned images, with no selectable text, can't be searched.
4. **Save**, then try it in the agent playground: *"Which glass helps keep a west-facing living room cool?"*

## 2. Give yourself permission to call it

Subscription Owner manages resources but may not include *using* the agent.
Azure portal → your Foundry resource (`voice-agent-gurpreet`) → **Access control (IAM)** → **Add role
assignment** → **Azure AI User** → select your account → **Review + assign**. Allow ~5 minutes.

## 3. Run it on your computer (Windows)

Needs Python 3.11+ and Git. In a terminal (VS Code: Ctrl+`), in the folder you cloned:
```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```
Put the Foundry key (Foundry Home page → API key) after `SPEECH_KEY=` in `.env`, then:
```
python -m scripts.check_agent
uvicorn app.main:app
```
The first run opens a browser window to sign in to Azure; use the account that owns the subscription.
Open http://localhost:8000 in **Chrome or Edge**, pick a language, tap the microphone and speak.

`scripts/check_agent.py` checks each link in turn (settings, Entra sign-in, the agent, Azure Speech) and says
what to fix when one fails.

### Or in GitHub Codespaces

Add a Codespaces secret `FOUNDRY_API_KEY` (the Foundry key), create a codespace on `main`, then run
`az login --use-device-code` before the two commands above.

## 4. Put it on a website (Azure App Service)

1. Azure portal → **Create a resource → Web App**. Resource group: the Foundry one. Name: e.g.
   `clearview-voice-agent`. Publish: **Code**. Runtime: **Python 3.12**. OS: **Linux**. Region: **East US 2**.
   Pricing plan: **Free F1** (enough for a demo). Create.
2. Web app → **Settings → Environment variables**, add: `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_AGENT_NAME`,
   `SPEECH_KEY`, `SPEECH_REGION`, `SPEECH_VOICE`, `APP_PASSWORD` (pick one), `WEBSITES_PORT` = `8000`. Apply.
3. Web app → **Settings → Configuration → Startup command**:
   `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`. Save.
4. Web app → **Settings → Identity → System assigned → On**. Then Foundry resource → **Access control (IAM)** →
   **Add role assignment → Azure AI User** → assign access to **Managed identity** → your web app. This is how the
   site signs in to the agent: no keys or passwords for Foundry are stored anywhere.
5. Web app → **Deployment Center** → Source **GitHub** → this repo, branch `main` → Save. Azure adds a GitHub
   Actions workflow and deploys on every push.
6. Open `https://<name>.azurewebsites.net`, sign in with any username and `APP_PASSWORD`.

## Settings (`.env`)

| Setting | What it is |
|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | Foundry Home page → **Project endpoint** (`…services.ai.azure.com/api/projects/<project>`) |
| `FOUNDRY_AGENT_NAME` | The agent's name in Foundry |
| `SPEECH_KEY` | Foundry resource key; blank = use `FOUNDRY_API_KEY` |
| `SPEECH_REGION` | The resource's region (`eastus2`) |
| `SPEECH_VOICE` | Azure neural voice; the default multilingual voice speaks every listed language |
| `APP_PASSWORD` | Optional. When set, the site asks for it (any username). Set it on any public deployment |

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
