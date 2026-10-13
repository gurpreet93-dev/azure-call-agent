# Foundry Voice Agent

Phone-style voice assistant for a glass supplier, in the caller's own language. The browser turns your speech into text, an agent in
**Microsoft Foundry** decides what to say, and **Azure Speech** speaks the reply.

The agent's instructions, knowledge base and model are configured **in Foundry only**. This app
holds no prompt and no documents; it references the agent by name.

```
mic ─► Azure Speech recognition (language auto-detected) ─► text
     ─► this app ─► Foundry agent: instructions + knowledge files + model (all in Foundry)
     ─► reply text ─► Azure Speech, multilingual neural voice ─► audio in the browser
```

| Part | Where it lives |
|---|---|
| Speech → text | Azure AI Speech, streaming from the browser (Speech SDK 1.51.0, bundled in `app/static/vendor`); Chrome's recognition as fallback |
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
   - This is a spoken phone call. Reply in one to three short sentences. Plain spoken sentences only: never use
     markdown, asterisks, bullet points, numbered lists, emojis or URLs.
   - Mention at most two or three products in one reply, then ask what matters most to the caller.
   - Ask one question at a time.

   Languages
   - Always reply in the language the caller is speaking. If they switch language, switch with them.
   - Callers' words may arrive written in English letters even when they speak an Indian language
     (for example "mujhe bataaoge" is Hindi). Recognise the language and reply in its own script:
     Hindi and Marathi in Devanagari, Bengali in Bengali script, Gujarati in Gujarati script, Punjabi in Gurmukhi,
     Tamil in Tamil script, Telugu in Telugu script, Kannada in Kannada script, Malayalam in Malayalam script,
     Odia in Odia script.
   - In every language keep product names and codes in English letters (SGG Planitherm, ST 167), and say numbers
     and units the way a native speaker would.
   - When replying in English, use Indian English wording and spellings.

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

## 4. Put it on a website (Vercel)

The site signs in to Foundry as a **service principal** (an Entra app identity with a client secret).
1. Azure portal → **Microsoft Entra ID → App registrations → New registration** → name `clearview-vercel` →
   Register. Copy **Application (client) ID** and **Directory (tenant) ID**. Then **Certificates & secrets →
   New client secret** → copy the **Value** (shown once).
2. Foundry resource → **Access control (IAM) → Add role assignment → Azure AI User** → Members: *User, group,
   or service principal* → `clearview-vercel`.
3. vercel.com → **Add New → Project** → import this GitHub repo. Environment variables:
   `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_AGENT_NAME`, `SPEECH_KEY`, `SPEECH_REGION`, `SPEECH_VOICE`,
   `APP_PASSWORD`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`. Deploy.
4. Open the `*.vercel.app` URL; sign in with any username and `APP_PASSWORD`.

`index.py` is the Vercel entrypoint; `.vercelignore` keeps tests and local files out of the bundle.

## 4b. Or on Azure App Service

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

## 5. Chunking lab: our own chunks in Azure AI Search (`search-chunking` branch)

Foundry's File search chunks the brochures itself, and the portal's "Import and vectorize data" wizard uses
Azure AI Search's default Text Split. Neither shows you the chunks. This lab does the chunking in our code, compares
two strategies, and pushes the chunks into Azure AI Search, where the Foundry agent can use them.

| Strategy | What it does |
|---|---|
| `fixed` | Default-splitter baseline: windows of 256 tokens with 64 overlap, blind to structure. Spec tables get cut mid-table, so a row can lose its header ("which column is the U-value?") and its heading ("single or double glazing?"). |
| `headings` | Splits on markdown headings, never inside a table (big tables split by rows, header row repeated), and starts each chunk with the heading path, e.g. `Saint-Gobain Infinity > Product specifications > Neutrals — Double Glazed Unit`. |

`rag/questions.json` holds 12 test questions; each lists what a chunk must contain to answer it on its own.

### Azure setup (portal, ~10 minutes)
1. **Create a resource → Azure AI Search**: same resource group and region as the Foundry resource; pricing tier
   **Free** (50 MB, 3 indexes; these indexes take about 1 MB). One Free service per subscription.
   Then **Settings → Keys**: copy the URL (Overview) and the **Primary admin key**.
2. **Foundry → Models + endpoints → Deploy model → `text-embedding-3-small`** (Standard or Global Standard).
3. In the Codespace: create a `kb/` folder and drag the brochure `.md` files into it (git-ignored, so the knowledge
   base stays out of the repo). Add to `.env`:
   ```
   SEARCH_ENDPOINT=https://<your-search>.search.windows.net
   SEARCH_ADMIN_KEY=<primary admin key>
   EMBEDDING_DEPLOYMENT=text-embedding-3-small
   FOUNDRY_API_KEY=<Foundry resource key>   # optional; also gives the index a vectorizer for the agent
   ```

### Run
```
python -m scripts.chunking_lab inspect     # sizes; writes kb_chunks/fixed.md and kb_chunks/headings.md to read
python -m scripts.chunking_lab coverage    # offline: does one chunk hold each whole answer?
python -m scripts.chunking_lab build       # creates clearview-kb-fixed and clearview-kb-headings, uploads chunks
python -m scripts.chunking_lab compare     # hybrid search: is an answering chunk in the top 3?
python -m scripts.chunking_lab compare --mode keyword    # or vector
```
Try other sizes: `--size 512 --overlap 128`, `--max-tokens 300`.

### Connect the index to an agent
Test on a **copy** so the live site keeps working: Foundry → Agents → `clearview-glass-agent` → duplicate it as
`clearview-glass-agent-search` → remove **File search** → **Knowledge → Add → Azure AI Search** → connect your
search service → index `clearview-kb-headings` → query type **Hybrid (vector + keyword)** (needs the vectorizer,
i.e. `FOUNDRY_API_KEY` set when you ran `build`; otherwise choose Simple/keyword). Ask both agents the questions in
`rag/questions.json` in the playground and compare. To switch the site over, set `FOUNDRY_AGENT_NAME` to the copy.

## Settings (`.env`)

| Setting | What it is |
|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | Foundry Home page → **Project endpoint** (`…services.ai.azure.com/api/projects/<project>`) |
| `FOUNDRY_AGENT_NAME` | The agent's name in Foundry |
| `SPEECH_KEY` | Foundry resource key; blank = use `FOUNDRY_API_KEY` |
| `SPEECH_REGION` | The resource's region (`eastus2`) |
| `SPEECH_VOICE` | Azure neural voice; the default multilingual voice speaks every listed language |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | Vercel only: the service principal the site signs in as |
| `APP_PASSWORD` | Optional. When set, the site asks for it (any username). Set it on any public deployment |

## Notes

- **Languages.** Azure Speech recognition detects the language as you talk, choosing between the picked
  language, Indian English and Hindi; the picker follows what it hears and what the agent replies in.
- **If Azure Speech fails**, the browser speaks the reply with its own voice and the reply bubble says so.
- **Voices.** Each language has a native Azure voice (Hindi: `hi-IN-SwaraNeural`, Indian English: `en-IN-NeerjaNeural`,
  …; table in `app/speech.py`, override with `SPEECH_VOICES`). Markdown and citation marks are stripped before speaking.
- **Interrupting.** One recognition session runs for the whole conversation, on a microphone stream with echo
  cancellation. Talking over a reply stops it and your sentence is sent; heard words that match the reply being
  spoken are treated as the agent's own echo and ignored. Tapping the mic during a reply also stops it.
- **Speech token.** The browser gets a 10-minute Azure Speech token from `/api/speech-token` (refreshed
  automatically); the Speech key never leaves the server.

## Tests

```
pip install -r requirements-dev.txt
pytest -q
```
Foundry and Azure Speech are replaced with fakes, so the tests run without an Azure account.
