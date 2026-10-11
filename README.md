# Azure Call Agent

An AI phone receptionist, with Claude running in **Microsoft Foundry** on Azure, for trade contractors (roofers, fabricators, applicators). A contractor
uploads their own documents; callers phone in; the agent answers questions from those documents and
**decides** what to do: book a site visit (Outlook invite to caller + contractor), email a preliminary
estimate, or escalate an emergency to the contractor.

## Built on Azure

| Job | Azure service | Status |
|---|---|---|
| The agent's model (Claude) | **Microsoft Foundry** model deployment | ✅ default (`LLM_PROVIDER=foundry`) |
| Outlook calendar invites + email | **Microsoft Graph** (Entra ID app registration) | ✅ optional; mock calendar/outbox without it |
| Knowledge-base search | Azure AI Search | planned (Pinecone today) |
| Phone line | Azure Communication Services | planned (Twilio today) |
| Hosting | Azure Container Apps | planned (runs locally today) |

The agent decides for itself which action fits the call; there is no fixed script. Switching
`LLM_PROVIDER=anthropic` runs the identical agent against the Anthropic API, as a fallback.

Product decisions and trade-offs: [`docs/decision-log.md`](docs/decision-log.md).
Step-by-step setup of Claude, Pinecone, ngrok, Twilio and Outlook: [`docs/integration-guide.md`](docs/integration-guide.md).

```
Caller ─► Twilio number ─► /voice/* webhooks ──────┐
Browser "test call" (mic + speaker) ─► /api/sim/* ─┤
                                                   ▼
                  Agent loop: Claude on Microsoft Foundry + tools (app/agent.py)
   ┌─────────────┬────────────────────┬───────────────┬────────────────────┬──────────┐
 knowledge     check_availability    send_proposal   escalate_to_         end_call
 (full prompt  book_site_visit       (KB prices      contractor
  or Pinecone) (Outlook / mock)       only)          (email alert)
```

## What it does

| Capability | Where |
|---|---|
| Contractor signup/login, settings (hours, visit length, notify email, Outlook mailbox) | `app/main.py`, `app/accounts.py` |
| Upload **Word / Excel / PDF / text** knowledge docs; view chunks; test retrieval | `app/knowledge.py` |
| **Automatic KB routing**: small KB → whole KB in the prompt (cached); KB over the threshold → chunks synced to **Pinecone** (one namespace per contractor) and the agent gets a search tool | `app/knowledge.py`, `app/vectorstore.py` |
| Agent that chooses between answering, booking, quoting, escalating | `app/agent.py`, `app/tools.py` |
| Outlook calendar + email via Microsoft Graph, or a built-in mock calendar/outbox | `app/integrations/` |
| Browser voice test call (Chrome speech recognition + speech synthesis) with live view of the agent's decisions | `app/templates/simulator.html` |
| Real phone calls via Twilio (speech in, speech out) | `app/voice.py` |
| Call log: transcript, every decision with inputs/results, emails and invites | `/calls` |
| Scenario eval: simulated callers × 10 scenarios, scored on actions + price grounding | `scripts/run_scenarios.py`, `evals/` |

## Run it locally (10 minutes)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # add FOUNDRY_RESOURCE + FOUNDRY_API_KEY + AGENT_MODEL (see below)
python -m scripts.check_foundry # proves the Foundry connection and tool use work
python -m app.seed              # demo contractor: demo@summit.test / demo1234 + sample KB
uvicorn app.main:app --reload
```

Open http://localhost:8000, log in, go to **Test call**, press **Start call** and talk (Chrome/Edge).
Watch the right-hand panel: every tool the agent decides to use appears live.

Without Pinecone or Microsoft keys everything still works: retrieval falls back to local keyword
search (BM25), and bookings/emails go to the built-in calendar and outbox (see **Emails & invites**).

## Connect Microsoft Foundry

1. In the Foundry portal (ai.azure.com), create a project, then **Models + endpoints → Deploy model**
   and deploy a Claude model (Claude Haiku 5.5, base model, Global Standard). Note the **deployment name**.
2. On the project's **Overview** page, copy the API key and the endpoint
   `https://<resource>.services.ai.azure.com`.
3. In `.env`: `FOUNDRY_RESOURCE=<resource>` (the name only), `FOUNDRY_API_KEY=<key>`,
   `AGENT_MODEL=<deployment name>`.
4. Run `python -m scripts.check_foundry`. It sends one message and one tool call, and explains
   any failure (wrong key, wrong deployment name, missing role, quota).

The app uses the Anthropic SDK's `AnthropicFoundry` client, so prompt caching and tool use work the
same as on the Anthropic API.

## Turn on Pinecone

1. Free Starter account at https://app.pinecone.io → API key → `PINECONE_API_KEY=...` in `.env`.
2. Restart. The index `call-agent-kb` is created on first use with integrated embedding
   (`llama-text-embed-v2`), so Pinecone embeds the text itself.
3. The switch is automatic: when a contractor's KB exceeds `KB_FULL_CONTEXT_MAX_TOKENS` (default
   30,000), all chunks are pushed to their namespace and the agent starts searching.
   **To demo the switch** with the small sample KB, set `KB_FULL_CONTEXT_MAX_TOKENS=1000`, restart,
   and upload any document: the knowledge page flips to "Retrieval mode · pinecone".

## Turn on real phone calls (Twilio)

1. Twilio account (trial credit is enough) → buy a voice number.
2. Expose your laptop: `ngrok http 8000` → set `PUBLIC_BASE_URL=https://<id>.ngrok.app`.
3. In the Twilio number's settings: **A call comes in** → Webhook `POST {PUBLIC_BASE_URL}/voice/incoming`;
   **Call status changes** → `POST {PUBLIC_BASE_URL}/voice/status`.
4. Put the number (E.164, e.g. `+14165550100`) in **Settings → Inbound number**. With a single
   contractor account, every call routes to it anyway.
5. Set `TWILIO_AUTH_TOKEN` to validate webhook signatures.

Twilio does speech-to-text (`<Gather input="speech">`) and text-to-speech (`<Say>`). Expect a few
seconds per turn; see decision D1/D7 for the upgrade path.

## Turn on Outlook (Microsoft Graph)

1. Entra ID → App registrations → New. Add **application** permissions `Calendars.ReadWrite` and
   `Mail.Send`, grant admin consent, create a client secret.
2. Set `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET`.
3. In **Settings → Outlook mailbox**, enter the contractor's mailbox (e.g. `alex@summitroofing.com`).
   Bookings now create real Outlook events and Outlook sends the invites to the caller and contractor.
   Free/busy is read from that calendar.

## Scenario eval

```bash
python -m scripts.run_scenarios                    # all 10 scenarios
python -m scripts.run_scenarios emergency-leak     # one
```

A second Claude instance plays each caller persona (`evals/scenarios.json`). Each call is checked for
the expected action, no forbidden actions (e.g. no proposal for metal roofs), every quoted price
existing in the KB, and a clean close. Results go to `evals/results-<timestamp>.md`. A few cents per run.

## Chunking experiment

```bash
python -m scripts.chunking_experiment                       # local keyword search
python -m scripts.chunking_experiment --backend pinecone    # semantic search (also: hybrid, --rerank)
```

Compares the strategies in `evals/chunking_strategies.json` (method, size, overlap, context label) on
`evals/retrieval_questions.json`, and writes a report with every chunk and what each question retrieved
to `evals/chunking/<timestamp>/`. Set `CHUNK_METHOD` / `CHUNK_MAX_CHARS` / `CHUNK_OVERLAP` /
`CHUNK_CONTEXT` in `.env` to the winner. Guide: [`docs/chunking-playbook.md`](docs/chunking-playbook.md).
Also try strategies live in the app: **Knowledge base → open a document → Chunking preview**.

## Tests

```bash
pytest -q
```

Covers parsing (Word headings and tables, Excel rows, plain-text headings), chunking, full-context vs
Pinecone routing (including automatic sync and deletes), tenant isolation, slot finding, booking and
double-booking, proposal maths and validation, the agent loop (scripted fake model), graceful API
failure, and the Twilio webhook flow.

## Demo script (3 minutes)

1. **Knowledge base**: show the two uploaded docs (text + Excel), the token meter and mode badge.
   Open a doc to show chunks with their `[file > section]` headers.
2. **Test call: emergency**: "Water is coming through my kitchen ceiling." Point at the live decision
   panel: escalation, then the earliest inspection slot. It does not promise a technician immediately.
3. **Test call: quote**: "Price for replacing 120 feet of 5 inch gutters?" → estimate emailed with tax,
   copied to the contractor. Then ask for a metal-roof price → it won't guess and offers an inspection.
4. **Calls**: open the record: transcript, every decision, the email and the calendar invite.
5. **Eval results**: "10 scenarios, X/10 passed; here's the one that failed and what I changed."

## Project layout

```
app/
  main.py            routes: auth, dashboard, KB, settings, calls, outbox, simulator API
  agent.py           system prompt + Claude tool-use loop (Foundry or Anthropic API) (append-only history, prompt caching)
  tools.py           agent tools: validation, slot finding, booking, proposals, escalation
  knowledge.py       parsing (docx/xlsx/pdf/txt), chunking, size-based routing, BM25 fallback
  vectorstore.py     Pinecone (namespace per contractor, integrated embedding)
  voice.py           Twilio webhooks (TwiML)
  integrations/      Microsoft Graph calendar + email, mock fallbacks
  templates/         dashboard UI
sample_kb/           Summit Roofing company info + DEMO price list (fictional prices)
evals/               caller scenarios + results
scripts/             eval runner, sample price list generator
tests/               pytest suite
docs/decision-log.md product decisions and trade-offs
```
