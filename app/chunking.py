"""Chunking strategies. One module used by both the app (settings from .env) and
scripts/chunking_experiment.py (strategies from evals/chunking_strategies.json).

Levers (see docs/chunking-playbook.md):
  method    where to cut
            fixed     - every N characters, ignoring meaning (naive baseline)
            sentence  - pack whole sentences up to N characters
            recursive - try paragraph breaks, then lines, then sentences, then words
            headings  - document-aware: never cross a heading; pack whole lines (rows, bullets)
  max_chars target chunk size
  overlap   characters of the previous chunk repeated at the start of the next
            (whole sentences/lines only, never mid-word; never across a heading)
  context   what each chunk is labelled with
            none      - raw text
            path      - "[file > Section > Subsection]" header (free)
            llm       - a sentence written by Claude situating the chunk in its document
                        ("contextual retrieval"; one Haiku call per chunk)

Input text is the normalised output of knowledge.extract_text (markdown '#' headings).
"""
import os
import re
from dataclasses import dataclass, replace

METHODS = ("fixed", "sentence", "recursive", "headings")
CONTEXTS = ("none", "path", "llm")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ChunkConfig:
    method: str = "headings"
    max_chars: int = 800
    overlap: int = 0
    context: str = "path"

    def __post_init__(self):
        if self.method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}")
        if self.context not in CONTEXTS:
            raise ValueError(f"context must be one of {CONTEXTS}")
        if not 0 <= self.overlap < self.max_chars:
            raise ValueError("overlap must be >= 0 and smaller than max_chars")

    @property
    def label(self) -> str:
        return f"{self.method}-{self.max_chars}-ov{self.overlap}-{self.context}"


def config_from_env() -> ChunkConfig:
    return ChunkConfig(
        method=os.getenv("CHUNK_METHOD", "headings"),
        max_chars=int(os.getenv("CHUNK_MAX_CHARS", "800")),
        overlap=int(os.getenv("CHUNK_OVERLAP", "0")),
        context=os.getenv("CHUNK_CONTEXT", "path"),
    )


# ---------------------------------------------------------------- packing helpers

def _pack(pieces: list[str], max_chars: int, overlap: int, joiner: str) -> list[str]:
    """Greedily pack pieces into chunks <= max_chars (a single oversized piece stands alone).
    Overlap: start each new chunk with the trailing pieces of the previous one whose total
    length fits in `overlap`."""
    chunks: list[list[str]] = []
    current: list[str] = []
    for piece in pieces:
        if current and len(joiner.join(current + [piece])) > max_chars:
            chunks.append(current)
            carry, size = [], 0
            for prev in reversed(current):
                if size + len(prev) > overlap:
                    break
                carry.insert(0, prev)
                size += len(prev) + len(joiner)
            current = carry if len(joiner.join(carry + [piece])) <= max_chars else []
        current.append(piece)
    if current:
        chunks.append(current)
    return [joiner.join(c) for c in chunks]


def _split_recursive(text: str, max_chars: int, separators=("\n\n", "\n", ". ", " ")) -> list[str]:
    if len(text) <= max_chars or not separators:
        return [text]
    sep, rest = separators[0], separators[1:]
    parts = [p for p in text.split(sep) if p.strip()]
    if len(parts) == 1:
        return _split_recursive(text, max_chars, rest)
    out = []
    for p in parts:
        out.extend(_split_recursive(p, max_chars, rest) if len(p) > max_chars else [p])
    return out


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """[(heading path, non-empty lines)] following markdown heading levels."""
    sections, stack, current = [], [], []

    def flush():
        if current:
            sections.append((" > ".join(h for _, h in stack), current))

    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            flush()
            current = []
            level = len(m.group(1))
            stack = [(lvl, h) for lvl, h in stack if lvl < level] + [(level, m.group(2).strip())]
        elif line.strip():
            current.append(line.strip())
    flush()
    return sections


def _path_at(text: str, offset: int) -> str:
    """Heading path in force at a character offset (for methods that ignore structure)."""
    stack = []
    for m in re.finditer(r"^(#{1,6})\s+(.*)$", text[:offset], flags=re.M):
        level = len(m.group(1))
        stack = [(lvl, h) for lvl, h in stack if lvl < level] + [(level, m.group(2).strip())]
    return " > ".join(h for _, h in stack)


# ---------------------------------------------------------------- strategies

def _raw_chunks(text: str, cfg: ChunkConfig) -> list[tuple[str, str]]:
    """[(section path, body)] before context labelling."""
    if cfg.method == "headings":
        out = []
        for path, lines in _sections(text):
            pieces = []
            for line in lines:  # a line longer than the budget is split on sentences
                pieces.extend(SENTENCE_RE.split(line) if len(line) > cfg.max_chars else [line])
            out += [(path, body) for body in _pack(pieces, cfg.max_chars, cfg.overlap, "\n")]
        return out

    if cfg.method == "fixed":
        step = cfg.max_chars - cfg.overlap
        bodies = [(i, text[i:i + cfg.max_chars]) for i in range(0, len(text), step)]
        return [(_path_at(text, i), b) for i, b in bodies if b.strip()]

    if cfg.method == "sentence":
        pieces = [s for line in text.splitlines() if line.strip() for s in SENTENCE_RE.split(line.strip())]
        bodies = _pack(pieces, cfg.max_chars, cfg.overlap, " ")
    else:  # recursive
        bodies = _pack(_split_recursive(text, cfg.max_chars), cfg.max_chars, cfg.overlap, "\n")

    out, cursor = [], 0
    for body in bodies:  # locate each chunk to recover its section for metadata/labels
        first = body.split("\n")[0][:40]
        pos = text.find(first, cursor)
        cursor = pos if pos >= 0 else cursor
        out.append((_path_at(text, cursor + 1), body))
    return out


def _llm_context(document: str, chunk: str) -> str:
    """Contextual retrieval: ask Claude for one sentence situating the chunk in its document."""
    from app.agent import client
    from app.config import settings

    resp = client().messages.create(
        model=settings.agent_model,
        max_tokens=120,
        system=[{"type": "text", "text": f"<document>\n{document}\n</document>",
                 "cache_control": {"type": "ephemeral"}}],  # same document for every chunk
        messages=[{"role": "user", "content":
                   f"<chunk>\n{chunk}\n</chunk>\nWrite one short sentence that situates this chunk within the "
                   "document to improve search retrieval of the chunk (what it is about, which product/policy). "
                   "Answer with only the sentence."}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def chunk(text: str, source: str, cfg: ChunkConfig | None = None) -> list[dict]:
    """Returns [{"section": str, "text": str}] where text is what gets embedded and returned."""
    cfg = cfg or config_from_env()
    out = []
    for path, body in _raw_chunks(text, cfg):
        body = body.strip()
        if not body:
            continue
        if cfg.context == "path":
            body = f"[{source}{' > ' + path if path else ''}]\n{body}"
        elif cfg.context == "llm":
            body = f"[{_llm_context(text, body)}]\n{body}"
        out.append({"section": path, "text": body})
    return out


def with_overrides(cfg: ChunkConfig, **kw) -> ChunkConfig:
    return replace(cfg, **{k: v for k, v in kw.items() if v is not None})
