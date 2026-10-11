"""Pinecone wrapper. The only module that knows Pinecone exists (decision log D2).

Uses an index with *integrated embedding*: Pinecone embeds `chunk_text` itself on upsert and
embeds the query text on search with the same model, so there is no separate embedding step.
One namespace per contractor means a search can never return another tenant's chunks.
"""
from functools import lru_cache

from app.config import settings

EMBED_MODEL = "llama-text-embed-v2"
UPSERT_BATCH = 90  # integrated-embedding upserts are limited per request; stay under ~96


def enabled() -> bool:
    return bool(settings.pinecone_api_key)


def namespace(contractor_id: int) -> str:
    return f"contractor-{contractor_id}"


@lru_cache(maxsize=1)
def _index():
    from pinecone import Pinecone

    pc = Pinecone(api_key=settings.pinecone_api_key)
    if not pc.indexes.exists(name=settings.pinecone_index):
        pc.indexes.create_for_model(
            name=settings.pinecone_index,
            cloud=settings.pinecone_cloud,
            region=settings.pinecone_region,
            embed={"model": EMBED_MODEL, "field_map": {"text": "chunk_text"}},
        )  # waits until the index is ready
    return pc.index(name=settings.pinecone_index)


def upsert(contractor_id: int, chunks: list[dict]) -> None:
    records = [
        {"_id": c["uid"], "chunk_text": c["text"], "source": c["filename"],
         "section": c["section"], "document_id": c["document_id"]}
        for c in chunks
    ]
    index = _index()
    for i in range(0, len(records), UPSERT_BATCH):
        index.upsert_records(records=records[i:i + UPSERT_BATCH], namespace=namespace(contractor_id))
    # Note: Pinecone indexes asynchronously - new chunks become searchable a few seconds later.


def delete(contractor_id: int, uids: list[str]) -> None:
    index = _index()
    for i in range(0, len(uids), 1000):
        index.delete(ids=uids[i:i + 1000], namespace=namespace(contractor_id))


def search(contractor_id: int, query: str, k: int) -> list[dict]:
    resp = _index().search(
        namespace=namespace(contractor_id),
        top_k=k,
        inputs={"text": query},
        fields=["chunk_text", "source", "section"],
    )
    return [
        {"id": hit.id, "source": hit.fields.get("source"), "text": hit.fields.get("chunk_text"),
         "score": round(hit.score, 3)}
        for hit in resp.result.hits
    ]
