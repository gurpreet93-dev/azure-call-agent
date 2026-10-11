# Chunking Playbook

How to choose a chunking strategy with evidence, using `scripts/chunking_experiment.py`.

**Your role:** you form the hypotheses, write the questions, choose which strategies to compare
(`evals/chunking_strategies.json`), read the outputs and make the call. The code is a test bench.

Chunking only affects **retrieval mode** (knowledge bases above `KB_FULL_CONTEXT_MAX_TOKENS`; decision log D6).
Small knowledge bases skip retrieval entirely.

---

## 1. The levers

### A. Chunking levers: how the text is cut

| Lever | Options in this project | What it trades | What to look for in the output |
|---|---|---|---|
| **Boundary method** | `fixed`: every N characters, ignoring meaning (the baseline)<br>`sentence`: pack whole sentences<br>`recursive`: try paragraph → line → sentence → word breaks<br>`headings`: document-aware; never crosses a heading and keeps lines (price rows, bullets) whole | Simplicity vs respecting meaning. `fixed` cuts facts in half; `headings` depends on the document having real headings | In `chunks/fixed-500-baseline.md`, look for facts cut in two, e.g. "Saturday: 9:00 AM" / "–2:00 PM" |
| **Size** (`max_chars`) | any, e.g. 300 / 600 / 800 / 1500 | Small chunks are precise but lose context; big chunks hold context but blur several topics into one embedding, and the agent must read more text per search | Recall vs the "Avg chars retrieved/question" column (cost and latency) |
| **Overlap** | characters repeated from the previous chunk (whole sentences/lines only; never across a heading) | Protects facts that straddle a boundary, at the cost of more chunks and duplicate results | Chunk count rises; check whether the straddling questions flip from ❌ to ✅ |
| **Context label** ("title method") | `none`: raw text<br>`path`: `[file > Section > Subsection]` header, free<br>`llm`: Claude writes one sentence placing the chunk in its document ("contextual retrieval"); one Haiku call per chunk, so enable it deliberately | A chunk saying "$1,150 per square" means nothing alone; a label restores what it's about. LLM labels cost money at upload time | Compare `headings-800 (no label)` vs `+ path` vs `+ llm context`, especially on paraphrased questions |
| **Special structures** | always on: Excel rows become self-describing lines (`Item: …; Price: …`); Word tables become one line per row; bullets stay whole | Keeps a price glued to its item | `chunks/*.md` for the price list |
| **Metadata** | `source`, `section`, `document_id` stored with each chunk | Not used for ranking. It enables citing where a price came from, deleting a replaced document's chunks, and filtering | Pinecone console → record fields |

### B. Retrieval levers: same chunks, different search

| Lever | Options | Why it matters |
|---|---|---|
| **Backend** | `--backend bm25`: keyword (BM25) search, free<br>`--backend pinecone`: semantic search on meaning<br>`--backend hybrid`: both, merged with reciprocal rank fusion (combines the two ranked lists) | Keyword search misses paraphrases ("travel" vs "service area"). Semantic search can miss exact names and SKUs. Hybrid search aims to get both |
| **top-k** | `--k 3` / `--k 5` | More results raise recall, but the agent reads more text per turn: slower and costlier on a phone call |
| **Reranking** | `--rerank` (Pinecone-hosted reranker) | Fetches 10 candidates, then a second model re-orders them by relevance. Usually improves MRR. [Verify the reranker model name in `scripts/chunking_experiment.py` against Pinecone's docs] |

### C. Known methods not implemented here (for interview breadth)

- **Semantic chunking:** cut where the meaning shifts, detected by comparing neighbouring sentences'
  embeddings. It needs embeddings at chunking time.
- **LLM / agentic chunking:** an LLM decides the boundaries. It's the most expensive option.
- **Parent–child ("small-to-big"):** search small chunks, then hand the agent the whole parent
  section. This is precise matching with full context.
- **Sentence-window:** retrieve one sentence and pass its neighbours along.
- **Query rewriting:** have the LLM rephrase the caller's question before searching.

If asked, explain when you'd reach for each and why you didn't need it at this scale.

---

## 2. Metrics: how to check the output

| Metric | Meaning | Good for |
|---|---|---|
| **recall@k** | % of questions where a correct chunk is in the top k | "Can the agent find the answer at all?" The headline number |
| **MRR** | Average of 1/rank of the first correct chunk (1st = 1.0, 2nd = 0.5, 3rd = 0.33) | Rewards putting the answer first |
| **Chunks** | How many chunks the strategy produces | Storage and Pinecone write units |
| **Avg chars retrieved/question** | Text the agent must read per search | Cost and latency on a live call |
| **End-to-end** | `python -m scripts.run_scenarios` with the chosen strategy in retrieval mode | Does better retrieval change what the agent *does*? |

A question counts as answered when a retrieved chunk contains all of its `must_contain` phrases.
If a phrase is cut across two chunks, it counts as a miss, which is exactly the failure you're
measuring.

---

## 3. Reading the outputs

Each run writes `evals/chunking/<timestamp>/`:

- **`summary.md`**: scores table, the best strategy, and a **question × strategy grid**. Each cell
  shows the rank at which the answer was found, or ❌.
  - A row that is ❌ everywhere is a **retrieval-method problem** (vocabulary), not a chunking problem.
  - A row that differs between strategies is where **chunking matters**.
- **`chunks/<strategy>.md`**: every chunk exactly as stored and embedded. Read the baseline next to
  `headings-800 + path`.
- **`retrieved/<strategy>.md`**: for each question, the top-k chunks with ✅/· marks. Use it to explain
  *why* a question failed.

In the app, **Knowledge base → open a document** has a **Chunking preview**. Change method, size,
overlap and label and watch the chunks change live. It's good for a 30-second demo moment.

---

## 4. Suggested experiment plan (one lever at a time)

| # | Hypothesis | Run | Compare |
|---|---|---|---|
| H1 | Structure-aware chunks beat fixed cuts | `--backend bm25` | fixed-500 vs sentence-600 vs recursive-600 vs headings-800 |
| H2 | Overlap rescues facts that straddle a cut | same | fixed-500 vs fixed-500 + overlap-100 |
| H3 | Semantic search fixes vocabulary misses | `--backend pinecone` | the all-❌ rows from H1 |
| H4 | Path labels help semantic search on paraphrased questions | `--backend pinecone` | headings-800 no label vs + path |
| H5 | LLM context labels beat path labels | `--backend pinecone --include-disabled` | + path vs + llm context (costs ~cents) |
| H6 | Hybrid search gets the best of both | `--backend hybrid` | best of H3 vs hybrid |
| H7 | Reranking improves ranking, not just recall | `--backend pinecone --rerank` | MRR with vs without |
| H8 | Bigger chunks trade cost for recall | best method at 300 / 800 / 1500 | recall vs chars retrieved |

Write each result as a line in the decision log (D4): hypothesis, result, decision.

### Be honest about noise

- With 32 questions, **one question = about 3 percentage points.** Don't declare a winner on a 1–2
  question difference.
- Grow the set to **50+ questions**, written in real callers' words. That is your highest-value
  input, and it's entirely yours.
- Questions written by someone who has just read the document reuse its words, which flatters
  keyword search. Phrase them as a homeowner would ("will you leave nails in my yard?").

---

## 5. First run (2026-10-03, keyword search, 32 questions, 3 documents)

| Strategy | recall@3 | MRR | Chunks | Avg chars retrieved |
|---|---|---|---|---|
| fixed-500 (baseline) | 75% | 0.65 | 29 | 1,348 |
| fixed-500 + overlap-100 | 75% | 0.67 | 36 | 1,317 |
| **sentence-600** | **81%** | **0.72** | 27 | 1,417 |
| sentence-600 + overlap-150 | 75% | 0.70 | 32 | 1,493 |
| recursive-600 | 75% | 0.67 | 30 | 1,293 |
| headings-300 + path | 75% | 0.66 | 64 | 757 |
| headings-800 (no label) | 78% | 0.65 | 37 | 1,018 |
| headings-800 + path | 78% | 0.65 | 37 | 1,177 |
| headings-1500 + path | 78% | 0.65 | 36 | 1,253 |

**Observations**
- The spread is 24–26 of 32 questions, which is within noise (point 4). No strategy has clearly won yet.
- About 5 questions fail under *every* strategy, e.g. "open on Saturdays", "how far do you travel",
  "freezing outside" vs "minus five degrees". These are vocabulary mismatches that keyword search
  can't bridge. **Next step: H3 with Pinecone.**
- The fixed-500 baseline visibly cuts "Saturday: 9:00 AM" / "–2:00 PM" across two chunks. That's the
  concrete example of why naive chunking is risky, even when the averages look similar.
- headings-800 retrieves the least text per question at equal-or-better recall (1,018–1,177 chars
  vs 1,348). That's cheaper and faster per search on a live call.
