# Decision Log

Each entry records what was decided, what else was considered, and the answer to give if asked about it in an interview.

Confidence tags: **[Certain]** = checked against a primary source; **[Likely]** = strong inference; **[Guessing]** = check before quoting.

---

## D1. Speech-to-text / text-to-speech: managed vs self-hosted

**Decision:** Build a browser voice demo first (Chrome Web Speech API). Then add a real phone line with Twilio's built-in speech. Do **not** self-host speech models.

**Key point:** Twilio is mainly the *phone line*: it owns the number and connects it to the telephone network. Local code can't receive a phone call. Self-hosting replaces only the speech layer, not the phone line.

| Option | Phone line | STT / TTS | Cost | Build effort | Demo value |
|---|---|---|---|---|---|
| A. Browser "call" | None | Chrome Web Speech API | Free | ~1 day | Good over screen-share; not a real call |
| B. Twilio end-to-end | Twilio number | Twilio built-in speech | A few cents per call [Guessing] | ~1 day | Strong: the interviewer dials a real number |
| C. Twilio line + local speech | Twilio streams raw call audio | faster-whisper + Piper or Kokoro | Saves the speech fees; still pays Twilio per minute | Weeks | Same as B for the caller |

**Why not C:** the models aren't the hard part. The hard part is the real-time audio work around them:
- **Turn detection:** knowing when the caller has finished speaking (voice-activity detection, VAD).
- **Barge-in:** stopping playback when the caller interrupts.
- **Phone audio quality:** narrowband 8 kHz audio lowers transcription accuracy [Likely].
- **Latency:** transcription, then the LLM, then voice generation, stacked one after another. Probably 2–3 s on a CPU before tuning [Guessing].

Managed speech costs roughly a cent or two per minute [Guessing]. That's a small saving compared with weeks of engineering plus ongoing operations.

**Caveat:** the Chrome Web Speech API is free to the developer, but Chrome sends the audio to Google's servers [Likely]. It is not truly local.

**When to revisit:** speech becomes a large share of the cost per call at scale, a data-residency or privacy requirement forbids third-party audio processing, or a target market has poor managed-speech accuracy (accents, languages).

**Interview answer:**
> "I evaluated self-hosting speech-to-text and text-to-speech with Whisper and Piper. At pilot volume, managed speech costs about a cent per minute, while self-hosting adds weeks of real-time audio work (turn detection, interruptions, latency) plus ops burden. I'd revisit at a scale where speech becomes a large share of the cost per call, or if data-residency requirements forced it."

---

## D2. Vector database: Pinecone (Starter, free)

**Decision:** Pinecone on the free Starter plan. One index, **one namespace per contractor**. Used only when a contractor's knowledge base is too large to load whole into the prompt (see D6). Embeddings come from Pinecone's hosted `llama-text-embed-v2`.

**Considered:**
- **Chroma.** Free, runs locally inside the app, no account needed. It reads as a prototype, and tenant isolation is only a filter applied in code.
- **pgvector.** The best long-term fit if tenant data lives in Postgres (transactions, joins). It's more setup for a demo.
- **Keyword-only BM25 search** (a standard keyword-ranking method). No embeddings needed and strong on exact terms, but it misses paraphrases ("my roof is leaking" vs "leak repair").

**Why Pinecone:**
- **Tenant isolation by design.** A query can't cross a namespace. That's stronger than "we filter on contractor_id", which depends on every query remembering the filter.
- **Managed service.** A small team doesn't run search infrastructure.
- **Hosted embeddings.** No embedding model to run on our side.

**Facts checked:** [Certain, pinecone.io/pricing, checked 2026-10-03]
- Starter is free, with no monthly minimum.
- The paid plans are Builder at $20/mo flat, Standard at a $50/mo minimum, and Enterprise at a $500/mo minimum.

**Starter limits** [Certain, Pinecone console, 2026-10-03]: 1M read units/month, 2M write units/month, 2 GB storage, 1 GB egress, up to 5 serverless indexes, **up to 100 namespaces per index**, 2 organisation members. Not available on Starter: dedicated read nodes, roles and permissions (RBAC), backups, and access to all clouds and regions.

**What the namespace cap means:** one namespace per contractor allows 100 contractors per index (500 across 5 indexes) on the free plan. Past that, either pay for a higher plan or move to one shared namespace with a `contractor_id` metadata filter. That trade-off swaps structural isolation for scale, which makes it a good interview question to raise yourself.

**Not checked:** about 5M free embedding tokens per month on Starter (a third-party source says so) [Likely]. Integrated embedding usage may be metered separately from read/write units; check the usage page after the first upload.

**Correction to the original reasoning:** a vector DB does *not* reduce LLM cost. The LLM still runs on every turn of the call. Retrieval only decides which text goes into the prompt. The cost lever is model choice (D3).

**Risks and how they're handled:**
- **Vendor and pricing risk:** Pinecone has raised paid-plan minimums before [Likely]. Retrieval sits behind a single `search()` function, so the provider can be swapped in an afternoon.
- **Network latency:** every lookup on a live call is a round trip to Pinecone. Measure it.
- **Indexing delay:** Pinecone indexes upserted records asynchronously [Certain, Pinecone docs]. A document uploaded seconds before a test call may not be searchable yet.

**Interview answer:**
> "I chose Pinecone with a namespace per contractor so tenant isolation is structural rather than a filter someone can forget. Retrieval sits behind one interface, so if pricing or latency became a problem I could move to pgvector without touching the agent."

---

## D3. LLM: Claude Haiku 4.5

**Decision:** Claude Haiku 4.5 (`claude-haiku-4-5`) for the call agent.

**Why:**
- **Cost.** [Certain, Anthropic model table cached 2026-09-25] Haiku 4.5 costs $1 per million input tokens and $5 per million output. Claude Opus 5.5 costs $4/$20 and Sonnet 5.5 costs $2/$10.
- **Latency.** On a phone call, response time matters more than deep reasoning. The agent's job is narrow: search the knowledge base, pick an action, collect details.

**Estimate:** about 12 turns and ~60k input tokens per 5-minute call, so about 6–8 cents per call on Haiku, and less with prompt caching [Likely]. Measure the real number from API usage logs once calls run.

**When to revisit:** if the scenario evaluation shows the wrong action being chosen on complex calls, try Sonnet 5.5 on those turns only.

---

## D4. Chunking strategy

*Applies only to the retrieval path (large knowledge bases, see D6). Small knowledge bases are loaded whole and need no chunking.*

**Decision:**
- Split at headings first.
- Pack whole paragraphs up to about 800 characters.
- Never split a table row.
- Start every chunk with its document name and section heading (a "contextual chunk header"), e.g. `[Price List > Metal Roofing]`.

**Why not a fixed 500-character split** (the approach in Pinecone's quickstart): it cuts a price row in half, separating "Metal roofing, standing seam" from "$1,150 per square". Then neither chunk answers "how much is a metal roof?"

**How it's validated:** `scripts/chunking_experiment.py` compares five strategies on `evals/retrieval_questions.json`, scoring recall@3 (does a correct chunk appear in the top 3 results?).

**First result** (local keyword search, 2026-10-03, 12 questions, 1.7K-token KB):

| Strategy | recall@3 | Chunks |
|---|---|---|
| fixed-500 (baseline) | 9/12 | 14 |
| headings-400 | 8/12 | 26 |
| headings-800 | 9/12 | 18 |
| headings-1200 | 9/12 | 16 |
| headings-800, no header | 9/12 | 17 |

**What it showed:** on a KB this small, chunking strategy made no measurable difference. Every miss was a vocabulary mismatch ("Saturdays" vs "Saturday", "cost" vs "Price", "travel" vs "service area… 40 miles"), which keyword search can't bridge.

**Conclusions:**
1. That's the argument for semantic search (Pinecone) or hybrid search.
2. For small KBs, full-context mode (D6) sidesteps retrieval entirely.
3. Chunking choices only start to matter at retrieval-mode sizes, so the experiment needs re-running with `--backend pinecone` and a larger document before claiming a winner.

**Second run** (keyword search, 32 questions, 3 documents including a Word handbook; 9 strategies covering method, size, overlap and labels): sentence-600 scored 81% recall@3 and the others 75–78%. That spread is within noise. Full table, observations and next experiments: `docs/chunking-playbook.md` §5.

**TODO:** run `--backend pinecone`, then `hybrid` and `--rerank`, per the playbook's experiment plan, and record a decision here.

---

## D5. Agent autonomy rules (by how reversible each action is and how costly a mistake would be)

| Action | Autonomy | Reasoning |
|---|---|---|
| Answer from the knowledge base | Full | Low risk if grounded in documents; says "I'll check" when it isn't |
| Book a site visit | Full | Reversible; the contractor gets the invite and can move it |
| Send a proposal | Restricted | Prices only from the knowledge base, labelled "preliminary", contractor copied |
| Emergency (active leak, safety) | Escalate immediately | The cost of a missed emergency is far higher than the cost of a false alarm |
| Discounts, complaints, commercial jobs | Human only | Brand and legal risk |

---

## D6. Load the whole knowledge base vs retrieve chunks (RAG)

**Decision:** Route by knowledge-base size.
- **Small** (under a threshold, initially ~30k tokens; tune it with the scenario evaluation): put the whole knowledge base in the system prompt and use prompt caching. No chunking, no vector search.
- **Large:** chunk, embed and retrieve through Pinecone (D2, D4).

Both paths sit behind the same interface, so the agent doesn't know which one ran.

**Facts:** [Certain, Anthropic model table and prompt-caching docs]
- Haiku 4.5 has a **200K-token** context window.
- Cache reads cost about **0.1×** the input price. Cache writes cost **1.25×** with the 5-minute expiry.
- Haiku 4.5 only caches a prompt prefix of **4,096 tokens or more**. A shorter prefix silently doesn't cache.

**Cost at small size:** an 8k-token knowledge base plus about 3k of instructions and tool definitions, over a 12-turn call [Likely]:

| Approach | Cost per call |
|---|---|
| Whole KB, no caching | ~$0.13 |
| Whole KB, with caching | ~$0.03 |
| RAG | about the same as caching |

At this size, cost doesn't decide it.

**Why load the whole knowledge base when it's small:**
- **No retrieval misses.** RAG's most common failure is the right chunk never being retrieved, so the model answers "I don't know" or guesses. With the whole knowledge base in context, that failure can't happen.
- **Better answers that combine documents**, e.g. a price from the price list plus a condition from the warranty page.
- **Less to build, run and debug.**

**Why retrieve when it's large:**
- **The hard limit:** a fabricator's product catalogues and spec sheets can exceed 200K tokens.
- **Quality:** a large, mostly irrelevant context dilutes attention, and accuracy drops [Likely].
- **Latency:** time to first token grows with prompt length [Likely]. On a voice call, every extra second shows.
- **Cost:** it grows with knowledge-base size × number of turns, even with caching.

**How to demo it:** a contractor with 3 short documents runs on the full-context path. Upload a large product catalogue and that contractor switches to Pinecone. The dashboard shows which path served each call.

**Interview answer:**
> "For a typical small contractor the whole knowledge base fits in the model's context, and with prompt caching that's cheaper and more reliable than retrieval, because nothing can be missed. RAG earns its place once a knowledge base outgrows the context window or starts hurting latency, so I route by size, behind one interface, and tune the threshold with evals rather than picking a number."

---

## D7. Implementation notes worth knowing in an interview

- **Phone channel = Twilio `<Gather input="speech">`** (D1 option B). Simplest path to a real number.
  Each turn is a webhook round trip with Twilio's own speech recognition, so expect a few seconds per
  turn [Likely]. Upgrade path: Twilio ConversationRelay (streams text over a websocket and supports
  interruptions).
- **Greeting is fixed text, not generated.** The caller hears something instantly, and the first
  LLM call happens only once they've spoken.
- **Prompt caching.** Tools + system prompt (+ the whole KB in full-context mode) form a stable prefix,
  and history is append-only, so each turn re-reads the cached prefix at ~0.1× price. The current
  time and caller ID go in the first user message, not the system prompt, because a changing system
  prompt would break the cache. Watch-out [Certain]: Haiku 4.5 only caches prefixes ≥ 4,096 tokens.
  The Summit demo prompt is roughly at that line [Likely], so check `cache_read_input_tokens` in the
  server logs.
- **Pinecone integrated embedding.** Pinecone embeds text on upsert and on query
  (`llama-text-embed-v2`), so there's no embedding code. Chunk IDs are deterministic
  (`c{contractor}-d{doc}-{n}`), so deleting or replacing a document removes exactly its vectors.
- **Graceful degradation.** If Pinecone fails, search falls back to local BM25. If Graph fails, the
  tool returns an error and the agent falls back to taking a message. If the Claude API fails, the
  caller hears a fixed apology and the call is still logged.
- **No promised times.** The KB forbids promising immediate attendance, so the escalation tool returns
  wording without specific callback times.
- **Known limitation: plain-text/PDF headings are guessed.** They're flat, so "GAF Timberline HDZ"
  isn't nested under "Asphalt Shingle Roofing". Word documents keep real heading levels. Recommend
  that contractors upload .docx.
- **Email over the phone is the weakest link.** The agent spells the address back letter by letter
  and the tool validates its format. Better in production: text the caller a confirmation link.
- **Single-process demo.** Pinecone syncs during the upload request. In production that would be a
  background job, with SQLite replaced by Postgres.
- **Eval grading is deterministic, not LLM-judged.** Pass/fail comes from which tools ran and whether
  every quoted price exists in the KB (or was computed from KB prices by the proposal tool). That
  makes results reproducible and explainable; the simulated caller is the only stochastic part.
