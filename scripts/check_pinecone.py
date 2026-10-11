"""One-command Pinecone check. Run:  python -m scripts.check_pinecone

1. reads PINECONE_API_KEY from .env
2. connects and creates the index if it doesn't exist (first time: about a minute)
3. stores 3 test sentences, searches them by meaning, prints what came back
4. deletes the test data
"""
import sys
import time

from app.config import settings

TEST_NS = "connection-test"
SENTENCES = [
    "Saturday hours are 9:00 AM to 2:00 PM.",
    "Our normal service area covers properties within approximately 40 miles of our office.",
    "Gutter guards cost 9 dollars per linear foot.",
]
QUESTION = "How far away do you travel for jobs?"


def step(n: int, text: str) -> None:
    print(f"\n[{n}/4] {text}", flush=True)


def main() -> None:
    step(1, "Reading your API key from .env")
    if not settings.pinecone_api_key:
        sys.exit("  ✗ PINECONE_API_KEY is empty. Open .env, paste the key after PINECONE_API_KEY=, save, retry.")
    print(f"  ✓ key found (starts with {settings.pinecone_api_key[:5]}…)")

    step(2, f"Connecting to Pinecone and preparing index '{settings.pinecone_index}'")
    from pinecone import Pinecone
    try:
        pc = Pinecone(api_key=settings.pinecone_api_key)
        existed = pc.indexes.exists(name=settings.pinecone_index)
    except Exception as exc:
        sys.exit(f"  ✗ Pinecone refused the connection: {exc}\n    Check the key was copied fully, with no spaces.")
    if not existed:
        print("  … creating it now (first time only, can take a minute)")
    from app import vectorstore
    try:
        index = vectorstore._index()
    except Exception as exc:
        sys.exit(f"  ✗ Couldn't create the index: {exc}\n"
                 f"    If it mentions region/cloud, the free plan may need PINECONE_REGION / PINECONE_CLOUD changed in .env.")
    print(f"  ✓ index ready ({'already existed' if existed else 'created'})")

    step(3, "Storing 3 test sentences and searching by meaning")
    index.upsert_records(records=[{"_id": f"t{i}", "chunk_text": s} for i, s in enumerate(SENTENCES)],
                         namespace=TEST_NS)
    print(f'  question: "{QUESTION}"')
    hits = []
    for _ in range(10):  # new records take a few seconds to become searchable
        time.sleep(3)
        hits = index.search(namespace=TEST_NS, top_k=3, inputs={"text": QUESTION},
                            fields=["chunk_text"]).result.hits
        if hits:
            break
    if not hits:
        sys.exit("  ✗ Stored the sentences but search returned nothing after 30s. Run again in a minute.")
    for rank, h in enumerate(hits, 1):
        print(f"  {rank}. score {h.score:.3f}  {h.fields.get('chunk_text')}")
    if "40 miles" in hits[0].fields.get("chunk_text", ""):
        print('  ✓ top answer is the "40 miles" sentence, even though the question never says "miles"'
              ' or "service area". That is semantic search working.')
    else:
        print("  ! search works, but the top result wasn't the expected one. Note it for the experiment.")

    step(4, "Cleaning up the test data")
    index.delete_namespace(namespace=TEST_NS)
    print("  ✓ done. Pinecone is connected and working.")


if __name__ == "__main__":
    main()
