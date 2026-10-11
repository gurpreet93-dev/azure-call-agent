# Integration Guide: from laptop to real phone calls

Do these in order. Each step ends with a **check** so you know it worked before moving on.
Steps 1–3 are essential for the interview; 4–5 are impressive extras; 6 is optional.

| Step | What | Time | Cost |
|---|---|---|---|
| 1 | Run locally + Anthropic key | 20 min | ~$5 prepaid credit |
| 2 | Run the scenario eval, fix what fails | 1–2 h | cents |
| 3 | Pinecone | 30 min | free (Starter) |
| 4 | ngrok (public URL for Twilio) | 15 min | free |
| 5 | Twilio phone number | 1 h | trial credit; upgrade likely needed (see 5.6) |
| 6 | Outlook via Microsoft Graph | 2–3 h | needs a Microsoft 365 business tenant |

Items marked [Verify] are details I couldn't confirm. Check them in the provider's console as you go.

---

## 1. Run locally with Claude

**1.1 Get the code**
```powershell
git clone https://github.com/gurpreet93-dev/Assistant.git
cd Assistant
git checkout claude/call-agent-platform-oxyyk9
```

**1.2 Python environment** (Python 3.11+)
```powershell
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
# macOS / Linux:  python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
If PowerShell refuses to run the activate script, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

**1.3 Anthropic API key**
1. Go to https://console.anthropic.com, sign up and add billing. Prepaid credit; a small top-up is
   plenty [Verify the minimum amount].
2. **API Keys → Create Key**. Copy it now; it's shown once.

**1.4 Configure**
```powershell
copy .env.example .env      # macOS/Linux: cp .env.example .env
```
Edit `.env`: set `FOUNDRY_RESOURCE`, `FOUNDRY_API_KEY` and `AGENT_MODEL` (see README → Connect Microsoft Foundry), or `LLM_PROVIDER=anthropic` with `ANTHROPIC_API_KEY=sk-ant-...`, and change `SESSION_SECRET` to any long random
string. The app loads `.env` automatically. **Never commit `.env`**; it's already in `.gitignore`.

**1.5 Seed and start**
```powershell
python -m app.seed
uvicorn app.main:app --reload
```

**Check**
- http://localhost:8000/health shows `"llm_configured": true`.
- Log in as `demo@summit.test` / `demo1234`, open **Test call → Start call**, and ask "Are you open
  Saturdays?" by typing or speaking (Chrome or Edge).
- The terminal log prints a `usage` line per model call. From the second turn onward,
  `cache_read_input_tokens` should be above 0. If it stays at 0, the prompt is under Haiku's
  4,096-token caching minimum. That isn't an error; it just costs slightly more.

---

## 2. Scenario eval (do this before any other integration)

```powershell
python -m scripts.run_scenarios
```

**Check:** the command prints a pass count and writes `evals/results-<timestamp>.md`.

For each failure, read the transcript, then change one of:
- the instructions (`INSTRUCTIONS` in `app/agent.py`),
- a tool description (`app/tools.py`),
- the knowledge-base document.

Re-run. Keep every results file: the before/after is your interview story.

---

## 3. Pinecone

**3.1** In https://app.pinecone.io: **API Keys → Create API key**. Copy it.

**3.2** In `.env`, set `PINECONE_API_KEY=...`. Leave `PINECONE_CLOUD=aws` and
`PINECONE_REGION=us-east-1`. Starter doesn't include all clouds and regions, and us-east-1 on AWS
is the usual free region [Verify in the console's "create index" screen].

**3.3 Run the chunking experiment with real semantic search**
```powershell
python -m scripts.chunking_experiment --backend pinecone
```
This creates the index `call-agent-kb` on first use (it takes a minute), runs each strategy in a
temporary namespace, and deletes the namespaces afterwards. Record the table in
`docs/decision-log.md` (D4).

**3.4 Demo the automatic switch**
1. In `.env`, set `KB_FULL_CONTEXT_MAX_TOKENS=1000` and restart uvicorn.
2. Upload any document on the **Knowledge base** page.
3. The badge changes to **Retrieval mode · pinecone** and "In Pinecone" shows e.g. `13/13`.
4. Set the threshold back to `30000` afterwards (or keep it low for the demo).

**Check**
- `/health` shows `"pinecone": true`.
- **Knowledge base → Test retrieval** shows backend `pinecone`.
- In the Pinecone console, the index has a namespace `contractor-1`.
- If search returns nothing right after an upload, wait about 10 seconds. Pinecone indexes
  asynchronously.

---

## 4. ngrok: give Twilio a URL that reaches your laptop

1. Sign up at https://ngrok.com, install the agent, and run `ngrok config add-authtoken <token>`.
2. Claim your free static domain: ngrok dashboard → **Domains** [Verify the menu name]. A fixed URL
   means you configure Twilio once instead of after every restart.
3. Start the tunnel:
   ```powershell
   ngrok http --url=your-name.ngrok-free.app 8000
   ```
   Older ngrok versions use `--domain=` instead of `--url=`.
4. In `.env`, set `PUBLIC_BASE_URL=https://your-name.ngrok-free.app` and restart uvicorn.

**Check:** `https://your-name.ngrok-free.app/health` opens in your browser.

---

## 5. Twilio phone number

**5.1** Sign up at https://www.twilio.com and verify your own mobile number during signup.

**5.2 Buy a number:** Console → **Phone Numbers → Manage → Buy a number**. Pick a local number with
**Voice** capability, ideally in your area code so the demo feels real. It's paid from trial credit.

**5.3 Point it at the app:** **Phone Numbers → Active numbers →** your number **→ Voice
configuration**:
- **A call comes in:** Webhook, `https://your-name.ngrok-free.app/voice/incoming`, HTTP POST.
- **Call status changes:** `https://your-name.ngrok-free.app/voice/status`, HTTP POST.
- Save.

**5.4** In the app, go to **Settings → Inbound number** and enter the number in E.164 format,
e.g. `+14165550100`.

**5.5** In `.env`, set `TWILIO_AUTH_TOKEN` (Console dashboard → Account Info) and restart. The app
now rejects webhook calls that aren't signed by Twilio.

**5.6 Trial-account limits (read before the interview)** [Verify on Twilio's trial page]
- Trial calls typically start with a Twilio "trial account" message.
- Trial accounts usually only connect calls from **verified** numbers.
- Either verify the interviewer's number in advance (awkward), or upgrade the account with a small
  top-up before 17 October.

**Check:** call the number from your verified mobile. You hear the greeting, can talk, and the call
appears under **Calls** with its transcript and decisions.

If you hear "an application error has occurred":
1. Is ngrok still running?
2. Is uvicorn running?
3. Does the `PUBLIC_BASE_URL` in `.env` exactly match the URL in Twilio? (A mismatch also fails the
   signature check.)
4. The ngrok inspector at http://127.0.0.1:4040 shows each request and response.

**Expect latency:** Twilio's built-in speech recognition plus a Claude call means a pause of a few
seconds per turn. Say this before the interviewer notices, and name the fix: streaming with Twilio
ConversationRelay (decision log D1/D7).

---

## 6. Outlook via Microsoft Graph (optional)

**Prerequisite:** a Microsoft 365 **work/business** tenant with an Exchange Online mailbox. A
personal Outlook.com account won't work with this app-only setup [Likely]. Options:
- a Microsoft 365 Business free trial [Verify current trial length], or
- the Microsoft 365 Developer Program sandbox, if you qualify [Verify eligibility].

If you can't get a tenant, skip this step. The built-in calendar sends real `.ics` invites into the
**Emails & invites** page, and you can explain that Graph is wired up behind a configuration switch.

**6.1** Go to https://entra.microsoft.com → **App registrations → New registration**. Name it
"Call Agent" and choose single tenant.

**6.2** Copy the **Application (client) ID** and **Directory (tenant) ID** from the Overview page.

**6.3** **Certificates & secrets → New client secret**. Copy the **Value** (not the Secret ID); it's
shown once.

**6.4** **API permissions → Add a permission → Microsoft Graph → Application permissions**:
- add `Calendars.ReadWrite` and `Mail.Send`;
- then **Grant admin consent**.

**6.5** In `.env`, set `MS_TENANT_ID`, `MS_CLIENT_ID` and `MS_CLIENT_SECRET`, then restart.

**6.6** In the app, go to **Settings → Outlook mailbox** and enter the mailbox address
(e.g. `alex@yourtenant.onmicrosoft.com`).

**Check**
- `/health` shows `"graph": true`.
- Book a visit in a test call. The event appears in that Outlook calendar, and the caller's email
  receives a real meeting request.
- **Emails & invites** shows provider `graph`.

**Security talking point:** application permissions can reach *every* mailbox in the tenant. In
production you'd restrict the app to the contractors' mailboxes with Exchange Online's
application-scoping controls (RBAC for Applications) [Verify the current feature name]. Least
privilege is a senior-PM answer.

---

## Before 17 October: demo-day checklist

- [ ] `.env` filled in; `/health` all green for what you'll demo.
- [ ] Prices in `sample_kb/summit-price-list-DEMO.xlsx` replaced or clearly labelled as demo.
- [ ] Latest eval results file saved, and you know your one best failure story.
- [ ] Chunking experiment re-run with `--backend pinecone`, table recorded in the decision log.
- [ ] Twilio upgraded or interviewer flow planned; tested a call from a second phone.
- [ ] Backup plan if the Wi-Fi or ngrok fails: the browser **Test call** on localhost, plus a
      2-minute screen recording of a phone call made the day before.
