"""Azure AI Search: one index per chunking strategy, filled with our own chunks and embeddings ("push" model).

We chunk and embed in our code, so the chunking strategy is ours, instead of the portal's "Import and vectorize
data" wizard, which chunks with its own Text Split defaults. The index also gets a vectorizer (the same embedding
model), so the Foundry agent's Azure AI Search tool can run vector and hybrid queries against it.

Settings (environment or .env):
  SEARCH_ENDPOINT          https://<your-search>.search.windows.net
  SEARCH_ADMIN_KEY         Search service -> Settings -> Keys -> Primary admin key
  AZURE_OPENAI_ENDPOINT    https://<foundry-resource>.openai.azure.com (worked out from FOUNDRY_PROJECT_ENDPOINT if unset)
  EMBEDDING_DEPLOYMENT     name of your embedding deployment (default text-embedding-3-small)
  FOUNDRY_API_KEY          optional: the Foundry resource key. Without it, embeddings use your Azure sign-in, and
                           the index gets no vectorizer (the agent can then only run keyword/semantic queries).
"""
import os
import re

import httpx

from app.config import settings
from rag.chunking import Chunk

API_VERSION = "2024-07-01"
DIMENSIONS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072, "text-embedding-ada-002": 1536}


class SearchError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def search_endpoint() -> str:
    ep = _env("SEARCH_ENDPOINT").rstrip("/")
    if not ep or not _env("SEARCH_ADMIN_KEY"):
        raise SearchError("Set SEARCH_ENDPOINT and SEARCH_ADMIN_KEY (Search service -> Settings -> Keys).")
    return ep


def openai_endpoint() -> str:
    ep = _env("AZURE_OPENAI_ENDPOINT").rstrip("/")
    if ep:
        return ep
    m = re.match(r"https://([^.]+)\.services\.ai\.azure\.com", settings.foundry_project_endpoint)
    if not m:
        raise SearchError("Set AZURE_OPENAI_ENDPOINT (Foundry resource -> Endpoints -> Azure OpenAI).")
    return f"https://{m.group(1)}.openai.azure.com"


def embedding_deployment() -> str:
    return _env("EMBEDDING_DEPLOYMENT", "text-embedding-3-small")


def embedding_model() -> str:
    """The model behind the deployment; the deployment is usually named after it."""
    return _env("EMBEDDING_MODEL", embedding_deployment())


# ------------------------------------------------------------------ embeddings

_client = None


def _openai():
    global _client
    if _client is None:
        from openai import AzureOpenAI
        key = _env("FOUNDRY_API_KEY")
        if key:
            _client = AzureOpenAI(azure_endpoint=openai_endpoint(), api_key=key, api_version="2024-10-21")
        else:
            from azure.identity import get_bearer_token_provider
            from app.foundry_agent import credential
            _client = AzureOpenAI(azure_endpoint=openai_endpoint(), api_version="2024-10-21",
                                  azure_ad_token_provider=get_bearer_token_provider(
                                      credential(), "https://cognitiveservices.azure.com/.default"))
    return _client


def embed(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), 16):
        resp = _openai().embeddings.create(model=embedding_deployment(), input=texts[i:i + 16])
        out += [d.embedding for d in resp.data]
    return out


# ------------------------------------------------------------------ index

def _call(method: str, path: str, body: dict | None = None, ok=(200, 201, 204)) -> dict:
    resp = httpx.request(method, f"{search_endpoint()}{path}", params={"api-version": API_VERSION},
                         headers={"api-key": _env("SEARCH_ADMIN_KEY")}, json=body, timeout=60)
    if resp.status_code not in ok:
        raise SearchError(f"{method} {path} failed ({resp.status_code}): {resp.text[:500]}")
    return resp.json() if resp.content else {}


def index_definition(name: str) -> dict:
    dims = DIMENSIONS.get(embedding_model(), 1536)
    vector_search = {"algorithms": [{"name": "hnsw", "kind": "hnsw"}],
                     "profiles": [{"name": "default", "algorithm": "hnsw"}]}
    if _env("FOUNDRY_API_KEY"):     # lets Search embed the agent's queries itself (vector / hybrid query types)
        vector_search["vectorizers"] = [{"name": "aoai", "kind": "azureOpenAI", "azureOpenAIParameters": {
            "resourceUri": openai_endpoint(), "deploymentId": embedding_deployment(),
            "modelName": embedding_model(), "apiKey": _env("FOUNDRY_API_KEY")}}]
        vector_search["profiles"][0]["vectorizer"] = "aoai"
    return {
        "name": name,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
            {"name": "title", "type": "Edm.String", "searchable": True, "analyzer": "en.microsoft"},
            {"name": "content", "type": "Edm.String", "searchable": True, "analyzer": "en.microsoft"},
            {"name": "source", "type": "Edm.String", "filterable": True, "facetable": True},
            {"name": "strategy", "type": "Edm.String", "filterable": True},
            {"name": "contentVector", "type": "Collection(Edm.Single)", "searchable": True,
             "dimensions": dims, "vectorSearchProfile": "default", "retrievable": False},
        ],
        "vectorSearch": vector_search,
        # Semantic ranker re-scores the top results with a language model. Free on small volumes; if your
        # service tier doesn't allow it, the index still works and queries fall back to plain hybrid.
        "semantic": {"configurations": [{"name": "default", "prioritizedFields": {
            "titleField": {"fieldName": "title"}, "prioritizedContentFields": [{"fieldName": "content"}]}}]},
    }


def recreate_index(name: str) -> None:
    _call("DELETE", f"/indexes/{name}", ok=(204, 404))
    definition = index_definition(name)
    try:
        _call("PUT", f"/indexes/{name}", definition)
    except SearchError as exc:
        if "semantic" not in str(exc).lower():
            raise
        definition.pop("semantic")                  # tier without semantic ranker: create the index without it
        _call("PUT", f"/indexes/{name}", definition)


def upload(name: str, chunks: list[Chunk], strategy: str) -> int:
    vectors = embed([c.content for c in chunks])
    docs = [{"@search.action": "upload", "id": c.id, "title": c.title, "content": c.content, "source": c.source,
             "strategy": strategy, "contentVector": v} for c, v in zip(chunks, vectors)]
    for i in range(0, len(docs), 100):
        result = _call("POST", f"/indexes/{name}/docs/index", {"value": docs[i:i + 100]})
        failed = [r for r in result.get("value", []) if not r.get("status")]
        if failed:
            raise SearchError(f"{len(failed)} chunks were rejected: {failed[:3]}")
    return len(docs)


def search(name: str, query: str, top: int = 3, mode: str = "hybrid") -> list[dict]:
    """mode: keyword | vector | hybrid (keyword + vector, merged by Reciprocal Rank Fusion)."""
    body: dict = {"top": top, "select": "id,title,content,source"}
    if mode in ("keyword", "hybrid"):
        body["search"] = query
    if mode in ("vector", "hybrid"):
        body["vectorQueries"] = [{"kind": "vector", "vector": embed([query])[0], "fields": "contentVector",
                                  "k": max(top, 10)}]
    return _call("POST", f"/indexes/{name}/docs/search", body).get("value", [])
