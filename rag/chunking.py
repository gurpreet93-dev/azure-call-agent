"""Two ways to cut the brochures into chunks for Azure AI Search, so they can be compared.

fixed     What a default splitter does (Azure AI Search's Text Split skill, LangChain's character splitter):
          windows of N tokens with an overlap, blind to the document's structure. A spec table gets cut wherever
          the window ends, so a row like "| Pristine White | Planitherm | PLT T | 75 | ... | 1.8 |" can land in a
          chunk without the header row that says which column is the U-value, or the heading that says whether
          it's single or double glazing.

headings  Structure-aware: split on markdown headings, never inside a table, and start every chunk with the
          document title and heading path ("contextual chunk header"). A table too big for one chunk is split by
          rows and every part repeats the table's header row.

Token counts are approximate (about 4 characters per token for English), which is all chunk sizing needs.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path


def tokens(text: str) -> int:
    return max(1, round(len(text) / 4))


@dataclass
class Chunk:
    source: str              # file name
    title: str               # heading path, e.g. "Saint-Gobain Infinity > Product specifications > Neutrals — DGU"
    content: str             # the text that is embedded and searched
    index: int = 0
    meta: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return re.sub(r"[^A-Za-z0-9_-]", "_", f"{Path(self.source).stem}-{self.index}")


# ------------------------------------------------------------------ fixed-size (the baseline)

def fixed(text: str, source: str, size: int = 256, overlap: int = 64) -> list[Chunk]:
    """Windows of `size` tokens, each starting `size - overlap` tokens after the last, on word boundaries."""
    words = text.split()
    max_chars, overlap_chars = size * 4, overlap * 4            # same 4-characters-per-token estimate as tokens()
    chunks, start = [], 0
    while start < len(words):
        end, length = start + 1, len(words[start])
        while end < len(words) and length + 1 + len(words[end]) <= max_chars:
            length += 1 + len(words[end])
            end += 1
        chunks.append(Chunk(source, Path(source).stem, " ".join(words[start:end])))
        if end >= len(words):
            break
        back, kept = end, 0                                    # next window re-reads the last `overlap` tokens
        while back > start + 1 and kept + 1 + len(words[back - 1]) <= overlap_chars:
            back -= 1
            kept += 1 + len(words[back])
        start = back
    for i, c in enumerate(chunks):
        c.index = i
    return chunks


# ------------------------------------------------------------------ structure-aware

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Group a section's lines into ('table', lines) and ('text', lines) blocks; blank lines separate text."""
    blocks, cur, kind = [], [], None
    for line in lines:
        k = "table" if line.lstrip().startswith("|") else ("blank" if not line.strip() else "text")
        if k == "blank":
            if cur:
                blocks.append((kind, cur))
            cur, kind = [], None
            continue
        if kind and k != kind:
            blocks.append((kind, cur))
            cur = []
        cur.append(line)
        kind = k
    if cur:
        blocks.append((kind, cur))
    return blocks


def _split_table(rows: list[str], budget: int) -> list[str]:
    """Split a markdown table by rows; every part repeats the header and separator rows."""
    head, body = rows[:2], rows[2:]
    parts, cur = [], []
    for r in body:
        if cur and tokens("\n".join(head + cur + [r])) > budget:
            parts.append("\n".join(head + cur))
            cur = []
        cur.append(r)
    if cur or not parts:
        parts.append("\n".join(head + cur))
    return parts


def _split_text(text: str, budget: int) -> list[str]:
    """Text longer than the budget: split between lines (list items, sentences), never mid-line if avoidable."""
    out, cur = [], []
    for line in text.split("\n"):
        if cur and tokens("\n".join(cur + [line])) > budget:
            out.append("\n".join(cur))
            cur = []
        cur.append(line)
    if cur:
        out.append("\n".join(cur))
    return out


def by_headings(text: str, source: str, max_tokens: int = 512) -> list[Chunk]:
    doc_title, path = Path(source).stem, []
    sections: list[tuple[list[str], list[str]]] = []          # (heading path, lines)
    lines: list[str] = []
    for line in text.split("\n"):
        m = HEADING.match(line)
        if m:
            if lines:
                sections.append((list(path), lines))
            level, title = len(m.group(1)), m.group(2).strip()
            if level == 1:
                doc_title, path = title, []
            else:
                path = path[:level - 2] + [title]
            lines = []
        else:
            lines.append(line)
    if lines:
        sections.append((list(path), lines))

    chunks: list[Chunk] = []
    for heads, body in sections:
        title = " > ".join([doc_title] + heads)
        header = f"{title}\n\n"
        budget = max_tokens - tokens(header)
        pieces: list[str] = []
        for kind, blines in _blocks(body):
            block = "\n".join(blines)
            if tokens(block) <= budget:
                pieces.append(block)
            elif kind == "table":
                pieces += _split_table(blines, budget)
            else:
                pieces += _split_text(block, budget)
        cur: list[str] = []
        for p in pieces:                                       # pack whole pieces into chunks
            if cur and tokens("\n\n".join(cur + [p])) > budget:
                chunks.append(Chunk(source, title, header + "\n\n".join(cur)))
                cur = []
            cur.append(p)
        if cur:
            chunks.append(Chunk(source, title, header + "\n\n".join(cur)))
    for i, c in enumerate(chunks):
        c.index = i
    return chunks


STRATEGIES = {"fixed": fixed, "headings": by_headings}


def chunk_folder(folder: Path, strategy: str, **opts) -> list[Chunk]:
    fn = STRATEGIES[strategy]
    out: list[Chunk] = []
    for f in sorted(Path(folder).glob("*.md")):
        out += fn(f.read_text(encoding="utf-8"), f.name, **opts)
    return out


# ------------------------------------------------------------------ evaluation

def answerable(chunk_text: str, must_contain: list[str]) -> bool:
    """A chunk can answer a question only if everything needed to read the answer is in that one chunk."""
    low = chunk_text.lower()
    return all(s.lower() in low for s in must_contain)


def coverage(chunks: list[Chunk], questions: list[dict]) -> list[dict]:
    """For each question: is there any chunk that holds the whole answer? Needs no search service at all."""
    return [{"question": q["question"],
             "answerable_chunks": [c.id for c in chunks if answerable(c.content, q["must_contain"])]}
            for q in questions]
