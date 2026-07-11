"""Semantic index for FAQ retrieval — cosine similarity over cached embeddings.

Improves recall for questions BM25 conservatively hands off (e.g. "Gibt es
Simulatoren?"), while staying "never wrong": the returned answer is still the
client's verbatim passage, and a similarity threshold gates confidence — below it
the caller falls back to BM25 and then to a handoff.

Embeddings are computed once and cached to disk (gitignored — they encode the
client's confidential FAQ text). The cache is keyed by a hash of the source texts so
it is rebuilt automatically when the knowledge base changes.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def texts_hash(texts: list[str]) -> str:
    return hashlib.sha256("||".join(texts).encode("utf-8")).hexdigest()[:16]


class SemanticIndex:
    def __init__(self, vectors: list[list[float]], docs: list[dict]):
        self.vectors = [_normalize(v) for v in vectors]
        self.docs = docs

    def search(self, query_vec: list[float], top_k: int = 1) -> list[tuple[float, int]]:
        q = _normalize(query_vec)
        sims = [(sum(a * b for a, b in zip(q, vec)), i) for i, vec in enumerate(self.vectors)]
        sims.sort(reverse=True)
        return sims[:top_k]

    # ---- persistence -------------------------------------------------------
    def save(self, path: str | Path, digest: str) -> None:
        Path(path).write_text(
            json.dumps({"hash": digest, "vectors": self.vectors, "docs": self.docs}),
            encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, digest: str) -> "SemanticIndex | None":
        p = Path(path)
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        if d.get("hash") != digest:
            return None
        idx = cls.__new__(cls)
        idx.vectors = d["vectors"]      # already normalized on save
        idx.docs = d["docs"]
        return idx

    @classmethod
    def build(cls, docs: list[dict], embed_fn, cache_path: str | Path | None = None
              ) -> "SemanticIndex | None":
        """docs need a 'source' and 'answer'; embedded text = source + answer.
        embed_fn(list[str]) -> list[vec] | None. Uses the disk cache when valid."""
        texts = [f"{d.get('source', '')}\n{d.get('answer', '')}".strip() for d in docs]
        digest = texts_hash(texts)
        if cache_path:
            cached = cls.load(cache_path, digest)
            if cached is not None:
                return cached
        vectors = embed_fn(texts)
        if not vectors:
            return None
        idx = cls(vectors, docs)
        if cache_path:
            try:
                idx.save(cache_path, digest)   # best-effort (data may be a read-only mount)
            except Exception:
                pass
        return idx
