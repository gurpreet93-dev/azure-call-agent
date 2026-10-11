import pytest

from app.chunking import ChunkConfig, chunk

DOC = "\n".join([
    "# Warranty",
    "Our workmanship warranty lasts ten years. It covers installation defects. It does not cover storm damage.",
    "Claims must be made in writing. We respond within five business days.",
    "## Transfers",
    "The warranty transfers once to a new owner. A transfer fee applies.",
    "# Payment",
    "- Deposit: 30% at signing",
    "- Balance: due on completion",
])


@pytest.mark.parametrize("method", ["fixed", "sentence", "recursive", "headings"])
def test_every_method_respects_size_and_keeps_all_text(method):
    chunks = chunk(DOC, "h.docx", ChunkConfig(method=method, max_chars=120, context="none"))
    assert all(len(c["text"]) <= 120 for c in chunks)
    joined = " ".join(c["text"] for c in chunks)
    for word in ("ten years", "transfers once", "30%", "completion"):
        assert word in joined


def test_headings_never_cross_a_section_and_label_the_path():
    chunks = chunk(DOC, "h.docx", ChunkConfig(method="headings", max_chars=400, context="path"))
    sections = [c["section"] for c in chunks]
    assert sections == ["Warranty", "Warranty > Transfers", "Payment"]
    assert chunks[1]["text"].startswith("[h.docx > Warranty > Transfers]")
    assert "Deposit" not in chunks[0]["text"]


def test_sentence_method_never_cuts_mid_sentence():
    from app.chunking import SENTENCE_RE
    whole = {s for line in DOC.splitlines() for s in SENTENCE_RE.split(line.strip())}
    for c in chunk(DOC, "h", ChunkConfig(method="sentence", max_chars=120, context="none")):
        pieces = [p for p in SENTENCE_RE.split(c["text"]) if p]
        rebuilt = " ".join(pieces)
        assert all(any(rebuilt_piece in w or w in rebuilt for w in whole) for rebuilt_piece in pieces)
        assert pieces[-1] in whole or any(pieces[-1].endswith(w) for w in whole)


def test_overlap_repeats_tail_of_previous_chunk():
    no = chunk(DOC, "h", ChunkConfig(method="sentence", max_chars=120, overlap=0, context="none"))
    ov = chunk(DOC, "h", ChunkConfig(method="sentence", max_chars=120, overlap=80, context="none"))
    assert len(ov) >= len(no)
    shared = [a["text"].split(". ")[-1] in b["text"] for a, b in zip(ov, ov[1:])]
    assert any(shared)


def test_fixed_overlap_steps_back():
    chunks = chunk("abcdefghij" * 10, "x", ChunkConfig(method="fixed", max_chars=30, overlap=10, context="none"))
    assert chunks[0]["text"][-10:] == chunks[1]["text"][:10]


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        ChunkConfig(method="magic")
    with pytest.raises(ValueError):
        ChunkConfig(max_chars=100, overlap=100)
