import json
import re

import pytest

from rag import chunking, search_index

DOC = """# Glass Brochure

## Overview
Intro paragraph about the glass range.

## Specs
Columns explained here.

### Double Glazed Unit
| Shade | Code | U-value |
|---|---|---|
""" + "\n".join(f"| Shade {i} | KT {100 + i} | 1.{i % 10} |" for i in range(60)) + """

## Contact
Phone 123.
"""


def test_headings_never_split_a_table_without_its_header():
    chunks = chunking.by_headings(DOC, "glass.md", max_tokens=200)
    table_chunks = [c for c in chunks if "KT 1" in c.content]
    assert len(table_chunks) > 1                                     # the table had to be split
    for c in table_chunks:
        assert "| Shade | Code | U-value |" in c.content            # every part repeats the header row
        assert c.title == "Glass Brochure > Specs > Double Glazed Unit"
        assert c.content.startswith(c.title)                         # contextual chunk header
        assert chunking.tokens(c.content) <= 200
    rows = [line for c in table_chunks for line in c.content.split("\n") if re.match(r"\| Shade \d", line)]
    assert len(rows) == 60                                           # no row lost or duplicated


def test_headings_keep_heading_path_and_reset_on_new_section():
    chunks = chunking.by_headings(DOC, "glass.md")
    titles = [c.title for c in chunks]
    assert "Glass Brochure > Overview" in titles
    assert "Glass Brochure > Contact" in titles
    assert len({c.id for c in chunks}) == len(chunks)


def test_fixed_windows_respect_size_and_overlap():
    text = " ".join(f"word{i}" for i in range(2000))
    chunks = chunking.fixed(text, "a.md", size=100, overlap=20)
    assert all(chunking.tokens(c.content) <= 100 for c in chunks)
    first, second = chunks[0].content.split(), chunks[1].content.split()
    assert first[-1] in second and second[0] in first                # windows overlap
    assert chunks[-1].content.split()[-1] == "word1999"              # nothing dropped at the end


def test_fixed_can_separate_a_row_from_its_header_but_headings_cannot():
    q = [{"question": "U-value of KT 155?", "must_contain": ["KT 155", "U-value", "Double Glazed"]}]
    fixed = chunking.coverage(chunking.fixed(DOC, "glass.md", size=80, overlap=10), q)
    headings = chunking.coverage(chunking.by_headings(DOC, "glass.md", max_tokens=200), q)
    assert not fixed[0]["answerable_chunks"]
    assert headings[0]["answerable_chunks"]


def test_questions_file_is_well_formed():
    from pathlib import Path
    qs = json.loads((Path(__file__).parents[1] / "rag" / "questions.json").read_text())
    assert qs and all(q["question"] and q["must_contain"] for q in qs)


# ------------------------------------------------------------------ Azure AI Search calls (mocked)

class FakeResp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}
        self.content = json.dumps(self._body).encode() if body is not None else b""
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


@pytest.fixture
def search_env(monkeypatch):
    monkeypatch.setenv("SEARCH_ENDPOINT", "https://s.search.windows.net/")
    monkeypatch.setenv("SEARCH_ADMIN_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://r.openai.azure.com")
    monkeypatch.setattr(search_index, "embed", lambda texts: [[0.1] * 1536 for _ in texts])
    calls = []
    return calls


def test_index_definition_adds_vectorizer_only_with_key(search_env, monkeypatch):
    monkeypatch.delenv("FOUNDRY_API_KEY", raising=False)
    d = search_index.index_definition("x")
    assert "vectorizers" not in d["vectorSearch"]
    assert next(f for f in d["fields"] if f["name"] == "contentVector")["dimensions"] == 1536
    monkeypatch.setenv("FOUNDRY_API_KEY", "fk")
    d = search_index.index_definition("x")
    v = d["vectorSearch"]["vectorizers"][0]["azureOpenAIParameters"]
    assert v["resourceUri"] == "https://r.openai.azure.com" and v["deploymentId"] == "text-embedding-3-small"
    assert d["vectorSearch"]["profiles"][0]["vectorizer"] == "aoai"


def test_recreate_retries_without_semantic(search_env, monkeypatch):
    sent = []

    def fake(method, url, params, headers, json, timeout):
        sent.append((method, json))
        if method == "PUT" and "semantic" in (json or {}):
            return FakeResp(400, {"error": {"message": "Semantic search is not enabled for this service"}})
        return FakeResp(201 if method == "PUT" else 204, {} if method == "PUT" else None)

    monkeypatch.setattr(search_index.httpx, "request", fake)
    search_index.recreate_index("clearview-kb-headings")
    assert [m for m, _ in sent] == ["DELETE", "PUT", "PUT"]
    assert "semantic" not in sent[-1][1]


def test_upload_and_hybrid_search_bodies(search_env, monkeypatch):
    sent = []

    def fake(method, url, params, headers, json, timeout):
        sent.append((url, json))
        if url.endswith("/docs/index"):
            return FakeResp(200, {"value": [{"key": d["id"], "status": True} for d in json["value"]]})
        return FakeResp(200, {"value": [{"id": "a", "title": "t", "content": "c", "source": "s"}]})

    monkeypatch.setattr(search_index.httpx, "request", fake)
    chunks = chunking.by_headings(DOC, "glass.md")
    assert search_index.upload("idx", chunks, "headings") == len(chunks)
    assert sent[0][0] == "https://s.search.windows.net/indexes/idx/docs/index"
    assert sent[0][1]["value"][0]["strategy"] == "headings"
    hits = search_index.search("idx", "U-value", mode="hybrid")
    body = sent[-1][1]
    assert hits[0]["id"] == "a" and body["search"] == "U-value" and body["vectorQueries"][0]["fields"] == "contentVector"
    search_index.search("idx", "U-value", mode="keyword")
    assert "vectorQueries" not in sent[-1][1]


def test_missing_settings_explain_themselves(monkeypatch):
    monkeypatch.delenv("SEARCH_ENDPOINT", raising=False)
    with pytest.raises(search_index.SearchError, match="SEARCH_ADMIN_KEY"):
        search_index.search_endpoint()
