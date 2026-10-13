"""Chunking lab: compare fixed-size and heading-aware chunking of the brochures, offline and in Azure AI Search.

Put the brochure markdown files in kb/ (git-ignored: the knowledge base is not kept in the repo), then:

  python -m scripts.chunking_lab inspect                   # chunk counts and sizes; writes kb_chunks/*.md to read
  python -m scripts.chunking_lab coverage                  # offline: can one chunk answer each test question?
  python -m scripts.chunking_lab build                     # Azure: one index per strategy, our chunks + embeddings
  python -m scripts.chunking_lab compare [--mode hybrid]   # Azure: does search return an answering chunk in the top 3?

Options: --kb FOLDER, --size 256 --overlap 64 (fixed), --max-tokens 512 (headings), --top 3,
--mode keyword|vector|hybrid.
"""
import argparse
import json
import sys
from pathlib import Path

from rag import chunking

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = json.loads((ROOT / "rag" / "questions.json").read_text(encoding="utf-8"))
INDEX = {"fixed": "clearview-kb-fixed", "headings": "clearview-kb-headings"}


def all_chunks(args) -> dict[str, list[chunking.Chunk]]:
    kb = Path(args.kb)
    if not list(kb.glob("*.md")):
        sys.exit(f"No .md files in {kb}/. Copy the brochure markdown files there first.")
    return {"fixed": chunking.chunk_folder(kb, "fixed", size=args.size, overlap=args.overlap),
            "headings": chunking.chunk_folder(kb, "headings", max_tokens=args.max_tokens)}


def inspect(args):
    out = ROOT / "kb_chunks"
    out.mkdir(exist_ok=True)
    for name, chunks in all_chunks(args).items():
        sizes = [chunking.tokens(c.content) for c in chunks]
        print(f"{name:9} {len(chunks):3} chunks   tokens: avg {sum(sizes) // len(sizes)}, "
              f"min {min(sizes)}, max {max(sizes)}")
        (out / f"{name}.md").write_text("\n\n".join(
            f"===== {c.id}  ({chunking.tokens(c.content)} tokens)\n{c.content}" for c in chunks), encoding="utf-8")
    print(f"\nRead the chunks in {out}/fixed.md and {out}/headings.md")


def coverage(args):
    results = {name: chunking.coverage(chunks, QUESTIONS) for name, chunks in all_chunks(args).items()}
    print(f"{'question':70} fixed  headings")
    for i, q in enumerate(QUESTIONS):
        marks = ["  yes " if results[n][i]["answerable_chunks"] else "  NO  " for n in ("fixed", "headings")]
        print(f"{q['question'][:68]:70}{marks[0]}  {marks[1]}")
    for n in ("fixed", "headings"):
        print(f"{n}: {sum(bool(r['answerable_chunks']) for r in results[n])}/{len(QUESTIONS)} questions have a "
              f"chunk that holds the whole answer")


def build(args):
    from rag import search_index
    for name, chunks in all_chunks(args).items():
        search_index.recreate_index(INDEX[name])
        n = search_index.upload(INDEX[name], chunks, name)
        print(f"{INDEX[name]}: {n} chunks uploaded")


def compare(args):
    from rag import search_index
    score = {n: 0 for n in INDEX}
    for q in QUESTIONS:
        print(f"\nQ: {q['question']}")
        for name, index in INDEX.items():
            hits = search_index.search(index, q["question"], top=args.top, mode=args.mode)
            rank = next((i + 1 for i, h in enumerate(hits) if chunking.answerable(h["content"], q["must_contain"])),
                        None)
            score[name] += rank is not None
            top = hits[0]["title"][:70] if hits else "(nothing)"
            print(f"  {name:9} {'rank ' + str(rank) if rank else 'MISS  '}   top result: {top}")
    print(f"\n{args.mode} search, answer in top {args.top}: " +
          ", ".join(f"{n} {s}/{len(QUESTIONS)}" for n, s in score.items()))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["inspect", "coverage", "build", "compare"])
    p.add_argument("--kb", default=str(ROOT / "kb"))
    p.add_argument("--size", type=int, default=256)
    p.add_argument("--overlap", type=int, default=64)
    p.add_argument("--max-tokens", type=int, default=512)
    p.add_argument("--top", type=int, default=3)
    p.add_argument("--mode", default="hybrid", choices=["keyword", "vector", "hybrid"])
    args = p.parse_args()
    try:
        {"inspect": inspect, "coverage": coverage, "build": build, "compare": compare}[args.command](args)
    except Exception as exc:
        from rag.search_index import SearchError
        if isinstance(exc, SearchError):
            sys.exit(f"Error: {exc}")
        raise


if __name__ == "__main__":
    main()
