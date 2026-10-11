"""Compare chunking strategies on your own questions, and look at what each one produces.

    python -m scripts.chunking_experiment                      # local keyword search (free, instant)
    python -m scripts.chunking_experiment --backend pinecone   # semantic search (Pinecone free tier)
    python -m scripts.chunking_experiment --backend hybrid     # keyword + semantic, merged
    python -m scripts.chunking_experiment --backend pinecone --rerank   # + Pinecone reranker
    python -m scripts.chunking_experiment --k 5 --include-disabled      # top-5, include LLM context

Inputs   sample_kb/*                         documents
         evals/retrieval_questions.json      question + text a correct chunk must contain
         evals/chunking_strategies.json      the strategies to compare (edit this)
Output   evals/chunking/<timestamp>/summary.md     scores, misses, question-by-strategy grid
         evals/chunking/<timestamp>/chunks/*.md    every chunk each strategy produced
         evals/chunking/<timestamp>/retrieved/*.md what each question retrieved, per strategy

Metrics  recall@k  share of questions with a correct chunk in the top k
         MRR       mean of 1/rank of the first correct chunk (rewards ranking it first)
         chunks / avg size / avg retrieved chars  (storage and how much text the agent must read)
"""
import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

from app import knowledge
from app.chunking import ChunkConfig, chunk

ROOT = Path(__file__).resolve().parent.parent
RERANK_MODEL = "bge-reranker-v2-m3"  # Pinecone-hosted reranker [verify name in Pinecone docs]


def load_docs() -> list[tuple[str, str]]:
    return [(p.name, knowledge.extract_text(p.name, p.read_bytes()))
            for p in sorted((ROOT / "sample_kb").iterdir()) if p.suffix.lower() in knowledge.SUPPORTED]


def is_hit(text: str, must: list[str]) -> bool:
    return all(m.lower() in text.lower() for m in must)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


# ---------------------------------------------------------------- backends: return ranked chunk indexes

def search_bm25(chunks: list[str], q: str, k: int) -> list[int]:
    return [i for i, _ in knowledge.bm25_rank(q, chunks)[:k]]


class PineconeNamespace:
    """Temporary namespace per strategy; deleted afterwards (Starter allows 100 per index)."""

    def __init__(self, name: str, chunks: list[str]):
        from app import vectorstore
        self.index, self.ns = vectorstore._index(), f"exp-{slug(name)}"
        records = [{"_id": str(i), "chunk_text": c} for i, c in enumerate(chunks)]
        for i in range(0, len(records), vectorstore.UPSERT_BATCH):
            self.index.upsert_records(records=records[i:i + vectorstore.UPSERT_BATCH], namespace=self.ns)
        time.sleep(12)  # indexing is asynchronous

    def search(self, q: str, k: int, rerank: bool) -> list[int]:
        kwargs = {"rerank": {"model": RERANK_MODEL, "rank_fields": ["chunk_text"], "top_n": k}} if rerank else {}
        hits = self.index.search(namespace=self.ns, top_k=max(k, 10) if rerank else k,
                                 inputs={"text": q}, fields=["chunk_text"], **kwargs).result.hits
        return [int(h.id) for h in hits][:k]

    def close(self):
        self.index.delete_namespace(namespace=self.ns)


def rrf(*rankings: list[int], k: int, c: int = 60) -> list[int]:
    """Reciprocal rank fusion: merge rankings from different searches without comparing their scores."""
    score: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            score[idx] = score.get(idx, 0) + 1 / (c + rank + 1)
    return sorted(score, key=score.get, reverse=True)[:k]


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["bm25", "pinecone", "hybrid"], default="bm25")
    ap.add_argument("--rerank", action="store_true", help="Pinecone reranker (pinecone/hybrid only)")
    ap.add_argument("--k", type=int, help="top-k (default from strategies file)")
    ap.add_argument("--include-disabled", action="store_true", help="also run strategies with enabled=false")
    args = ap.parse_args()

    cfg_file = json.loads((ROOT / "evals" / "chunking_strategies.json").read_text())
    k = args.k or cfg_file.get("top_k", 3)
    strategies = [s for s in cfg_file["strategies"] if s.get("enabled", True) or args.include_disabled]
    questions = json.loads((ROOT / "evals" / "retrieval_questions.json").read_text())
    docs = load_docs()
    backend = args.backend + (" + rerank" if args.rerank else "")
    out_dir = ROOT / "evals" / "chunking" / datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / "chunks").mkdir(parents=True)
    (out_dir / "retrieved").mkdir()
    print(f"{len(docs)} docs, {len(questions)} questions, {len(strategies)} strategies, backend={backend}, k={k}\n")

    results = []
    for s in strategies:
        cfg = ChunkConfig(method=s["method"], max_chars=s["max_chars"], overlap=s.get("overlap", 0),
                          context=s.get("context", "none"))
        chunks = [c["text"] for src, text in docs for c in chunk(text, src, cfg)]
        pine = PineconeNamespace(s["name"], chunks) if args.backend in ("pinecone", "hybrid") else None
        ranks, retrieved_chars, lines = [], [], [f"# {s['name']} - retrieved (top {k}, {backend})\n"]
        try:
            for q in questions:
                if args.backend == "bm25":
                    top = search_bm25(chunks, q["q"], k)
                elif args.backend == "pinecone":
                    top = pine.search(q["q"], k, args.rerank)
                else:
                    top = rrf(search_bm25(chunks, q["q"], 10), pine.search(q["q"], 10, args.rerank), k=k)
                rank = next((r + 1 for r, i in enumerate(top) if is_hit(chunks[i], q["must_contain"])), None)
                ranks.append(rank)
                retrieved_chars.append(sum(len(chunks[i]) for i in top))
                lines.append(f"## {'✅' if rank else '❌'} {q['q']}\n_needs: {q['must_contain']}_\n")
                lines += [f"{r + 1}. {'✅' if is_hit(chunks[i], q['must_contain']) else '·'} "
                          f"```{chunks[i][:300]}```\n" for r, i in enumerate(top)]
        finally:
            if pine:
                pine.close()
        found = [r for r in ranks if r]
        results.append({
            "name": s["name"], "cfg": cfg.label, "ranks": ranks,
            "recall": len(found) / len(questions), "mrr": sum(1 / r for r in found) / len(questions),
            "chunks": len(chunks), "avg_chars": sum(map(len, chunks)) // max(len(chunks), 1),
            "avg_retrieved": sum(retrieved_chars) // len(questions),
        })
        (out_dir / "chunks" / f"{slug(s['name'])}.md").write_text(
            f"# {s['name']} ({cfg.label}) - {len(chunks)} chunks\n\n" +
            "\n".join(f"### Chunk {i} ({len(c)} chars)\n```\n{c}\n```\n" for i, c in enumerate(chunks)))
        (out_dir / "retrieved" / f"{slug(s['name'])}.md").write_text("\n".join(lines))
        r = results[-1]
        print(f"  {s['name']:30} recall@{k} {r['recall']:.0%}  MRR {r['mrr']:.2f}")

    # summary
    best = max(results, key=lambda r: (r["recall"], r["mrr"], -r["avg_retrieved"]))
    md = [f"# Chunking experiment {out_dir.name}", "",
          f"Backend **{backend}** · top-{k} · {len(questions)} questions · documents: "
          + ", ".join(d for d, _ in docs), "",
          f"| Strategy | recall@{k} | MRR | Chunks | Avg chunk chars | Avg chars retrieved/question |",
          "|---|---|---|---|---|---|"]
    md += [f"| {'**' + r['name'] + '**' if r is best else r['name']} | {r['recall']:.0%} | {r['mrr']:.2f} | "
           f"{r['chunks']} | {r['avg_chars']} | {r['avg_retrieved']:,} |" for r in results]
    md += ["", f"Best by recall, then MRR, then least text retrieved: **{best['name']}** (`{best['cfg']}`).", "",
           "## Question × strategy (rank of first correct chunk; ❌ = not in top k)", "",
           "| Question | " + " | ".join(r["name"] for r in results) + " |",
           "|---|" + "---|" * len(results)]
    for qi, q in enumerate(questions):
        md.append(f"| {q['q']} | " + " | ".join(str(r["ranks"][qi]) if r["ranks"][qi] else "❌" for r in results) + " |")
    md += ["", "Open `chunks/<strategy>.md` to read every chunk and `retrieved/<strategy>.md` to see what each "
           "question pulled back."]
    (out_dir / "summary.md").write_text("\n".join(md))
    print(f"\nBest: {best['name']}\nReport: {out_dir.relative_to(ROOT)}/summary.md")


if __name__ == "__main__":
    main()
