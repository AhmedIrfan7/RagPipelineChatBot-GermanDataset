"""Deterministic knowledge retrieval for general (non-price) questions.

Answers questions like opening hours, required documents, registration, simulator,
funding — by retrieving the client's OWN text (FAQ answers and info sections) with a
BM25 ranker. No LLM: the returned answer is the client's verbatim passage, so it can't
be fabricated. Below a confidence threshold the caller hands off instead of guessing.

Source: data/processed/knowledge/ (gitignored; the extracted Informationsbogen +
consultation docs). This module holds only retrieval logic, no client data.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

_TOKEN = re.compile(r"[a-zA-Z0-9äöüßÄÖÜ]+")

_STOP = {
    "der", "die", "das", "und", "oder", "für", "ich", "mich", "wie", "wann", "wo",
    "was", "welche", "welcher", "gibt", "es", "ein", "eine", "einen", "am", "auch",
    "zur", "zum", "den", "dem", "ist", "sind", "habe", "kann", "muss", "auf", "in",
    "an", "bei", "von", "mit", "der", "des", "sich", "man", "im", "wir", "sie", "bitte",
    "the", "a", "an", "is", "are", "do", "does", "how", "what", "which", "where", "when",
    "i", "for", "of", "to", "at", "can", "much", "there", "me",
    # class tokens are the pricing path's job — they must not drive FAQ retrieval
    "klasse", "class", "führerschein", "licence", "license",
    # generic verbs/fillers that otherwise become spurious anchors
    "stehen", "steht", "verfügung", "verfügbar", "machen", "gibt", "gibts",
    "brauche", "benötige", "benötigt", "möchte", "bekomme", "erhalte", "haben",
    "get", "available", "need", "want", "make",
}

# small EN->DE expansion so common English questions hit the German corpus
_EN2DE = {
    "hours": "öffnungszeiten", "opening": "öffnungszeiten", "documents": "unterlagen",
    "document": "unterlagen", "register": "anmeldung", "registration": "anmeldung",
    "simulator": "simulator", "simulators": "simulator", "vehicles": "fahrzeuge",
    "vehicle": "fahrzeuge", "valid": "gültig", "validity": "gültig", "funding": "förder",
    "theory": "theorie", "online": "online", "cost": "kosten", "location": "standort",
    "address": "adresse", "phone": "telefon", "contact": "kontakt", "eye": "sehtest",
    "firstaid": "erste-hilfe", "first": "erste", "aid": "hilfe",
}


# targeted morphological normalization so inflected/compound forms match
# (e.g. "melde"/"anmelden"/"Anmeldeformulare" -> "anmeld"). First matching rule wins.
_CANON = [
    ("anmeld", "anmeld"), ("meld", "anmeld"),
    ("öffnung", "öffnungszeit"), ("uhrzeit", "öffnungszeit"),
    ("unterlage", "unterlagen"), ("dokument", "unterlagen"),
    ("gültig", "gültig"),
    ("sehtest", "sehtest"),
    ("hilfe", "erstehilfe"),
    ("simulator", "simulator"),
    ("fahrzeug", "fahrzeug"),
    ("förder", "förder"), ("finanzier", "förder"),
    ("theore", "theorie"),
    ("online", "online"),
    ("prüf", "prüfung"),
    ("standort", "standort"), ("adresse", "standort"),
    ("telefon", "kontakt"), ("kontakt", "kontakt"),
    ("intensiv", "intensiv"),
]


def _canon(token: str) -> str:
    for sub, canon in _CANON:
        if sub in token:
            return canon
    return token


def _tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(text.lower()):
        if len(t) < 2 or t in _STOP:      # drop single letters (e.g. class letter "b")
            continue
        out.append(_canon(_EN2DE.get(t, t)))
    return out


def parse_faqs(full_text: str) -> list[dict]:
    """Extract 'N. question?\\nanswer...' pairs from the Informationsbogen text."""
    lines = full_text.splitlines()
    q_re = re.compile(r"^\s*\d+\.\s*(.+\?)\s*$")
    sec_re = re.compile(r"^[1-9]\.\s+[A-ZÄÖÜ][^?]{2,48}$")
    faqs, i = [], 0
    while i < len(lines):
        m = q_re.match(lines[i].strip())
        if m:
            q = m.group(1).strip()
            ans, j = [], i + 1
            while j < len(lines):
                lj = lines[j].strip()
                if q_re.match(lj) or sec_re.match(lj):
                    break
                if lj:
                    ans.append(lj)
                j += 1
            if ans:
                faqs.append({"question": q, "answer": " ".join(ans)})
            i = j
        else:
            i += 1
    return faqs


# meta sections that describe the assistant/attachments rather than answer users;
# they contain the example queries themselves, so they must NOT be retrieval targets.
_META_TITLES = {"Ressourcen & Anhänge", "Einleitung"}
_META_MARKERS = ("Testabfragen", "Beispiel-Queries", "Prototyp des digitalen")


def _is_meta(title: str, text: str) -> bool:
    return title in _META_TITLES or any(m in text for m in _META_MARKERS)


def build_docs(knowledge_dir: Path) -> list[dict]:
    docs: list[dict] = []
    chunks_path = knowledge_dir / "rag_chunks.json"
    info_path = knowledge_dir / "informationsbogen.txt"
    if chunks_path.exists():
        for c in json.loads(chunks_path.read_text(encoding="utf-8")):
            title = c.get("section_title") or c.get("doc") or ""
            if _is_meta(title, c["text"]):
                continue
            docs.append({
                "kind": "section", "source": title,
                "index_text": c["text"], "answer": c["text"].strip(),
            })
    if info_path.exists():
        for f in parse_faqs(info_path.read_text(encoding="utf-8")):
            # weight the question by repeating it in the indexed text
            docs.append({
                "kind": "faq", "source": f["question"],
                "index_text": (f["question"] + " ") * 2 + f["answer"],
                "answer": f["answer"],
            })
    return docs


class KnowledgeBase:
    K1, B = 1.5, 0.75
    HYBRID_W = 0.06        # weight of BM25 when blended with cosine in semantic re-rank
    CAND_FLOOR = 0.35      # min cosine to enter re-ranking (final acceptance is stricter)

    def __init__(self, docs: list[dict], min_score: float = 4.0,
                 semantic=None, embed_query=None, sim_threshold: float = 0.45):
        self.docs = docs
        self.min_score = min_score
        self.semantic = semantic          # optional SemanticIndex
        self.embed_query = embed_query    # callable(str) -> vector | None
        self.sim_threshold = sim_threshold
        self.doc_tokens = [_tokens(d["index_text"]) for d in docs]
        self.dl = [len(t) for t in self.doc_tokens]
        self.avgdl = (sum(self.dl) / len(self.dl)) if self.dl else 0.0
        self.df: dict[str, int] = {}
        for toks in self.doc_tokens:
            for term in set(toks):
                self.df[term] = self.df.get(term, 0) + 1
        self.N = len(docs)
        self.tf = [{} for _ in docs]
        for i, toks in enumerate(self.doc_tokens):
            for term in toks:
                self.tf[i][term] = self.tf[i].get(term, 0) + 1

    @classmethod
    def from_dir(cls, knowledge_dir: str | Path, min_score: float = 4.0) -> "KnowledgeBase":
        return cls(build_docs(Path(knowledge_dir)), min_score=min_score)

    def attach_semantic(self, semantic, embed_query) -> None:
        self.semantic, self.embed_query = semantic, embed_query

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log((self.N - df + 0.5) / (df + 0.5) + 1)

    def score(self, query_tokens: list[str], i: int) -> float:
        s, dl = 0.0, self.dl[i] or 1
        for term in set(query_tokens):
            f = self.tf[i].get(term, 0)
            if not f:
                continue
            idf = self._idf(term)
            s += idf * (f * (self.K1 + 1)) / (f + self.K1 * (1 - self.B + self.B * dl / self.avgdl))
        return s

    def search(self, query: str, lang: str = "de") -> dict | None:
        # 1) semantic first (better recall) — gated by a cosine-similarity threshold
        sem = self._semantic_search(query)
        if sem is not None:
            return sem
        # 2) precision-first BM25 fallback
        return self._bm25_search(query)

    def _semantic_search(self, query: str) -> dict | None:
        if not (self.semantic and self.embed_query):
            return None
        qv = self.embed_query(query)
        if not qv:
            return None
        # embeddings generate CANDIDATES (recall); BM25 re-ranks them (precision), so a
        # high-cosine but off-topic section can't beat the section that shares the query's
        # keywords. Only for pure-paraphrase matches (no term overlap at all) do we defer
        # to cosine, and then require a keyword-consistency check to stay on topic.
        # generate candidates at a lower floor, then re-rank by a cosine+BM25 blend so a
        # keyword-bearing section can beat a slightly-higher-cosine but off-topic one.
        cand = [(sim, i) for sim, i in self.semantic.search(qv, top_k=8)
                if sim >= self.CAND_FLOOR]
        if not cand:
            return None
        qt = _tokens(query)
        scored = sorted(((sim + self.HYBRID_W * self._bm25_score_or_0(qt, i),
                          sim, self._bm25_score_or_0(qt, i), i) for sim, i in cand),
                        reverse=True)
        _, sim, bm, i = scored[0]
        # accept a confident cosine, OR a keyword-supported match with a lower cosine;
        # reject pure-semantic winners that don't even share a term (off-topic).
        if not (sim >= self.sim_threshold or (bm > 0 and sim >= self.CAND_FLOOR)):
            return None
        if bm <= 0 and set(qt) and not (set(qt) & set(self.doc_tokens[i])):
            return None
        d = self.docs[i]
        answer = d["answer"]
        if len(answer) > 700:
            answer = answer[:700].rsplit(" ", 1)[0] + " …"
        return {"answer": answer, "source": d["source"], "kind": d["kind"],
                "score": round(sim, 3), "retriever": "semantic"}

    def _bm25_score_or_0(self, query_tokens: list[str], i: int) -> float:
        return self.score(query_tokens, i) if query_tokens else 0.0

    def _bm25_search(self, query: str) -> dict | None:
        qt = _tokens(query)
        if not qt:
            return None
        # precision anchor: the answer MUST contain the query's most distinctive
        # (highest-IDF) term. Prevents semantically-wrong lexical matches — we would
        # rather hand off than return a confident wrong passage.
        anchor = max(set(qt), key=self._idf)
        if self._idf(anchor) <= 0:
            return None
        candidates = [i for i in range(self.N) if anchor in set(self.doc_tokens[i])]
        if not candidates:
            return None
        best_i = max(candidates, key=lambda i: self.score(qt, i))
        best_score = self.score(qt, best_i)
        if best_score < self.min_score:
            return None
        # coverage gate: a multi-term query must match >=2 of its terms in the answer,
        # so a single shared word can't carry a semantically-wrong match.
        content = set(qt)
        if len(content) >= 2 and len(content & set(self.doc_tokens[best_i])) < 2:
            return None
        d = self.docs[best_i]
        answer = d["answer"]
        if len(answer) > 700:
            answer = answer[:700].rsplit(" ", 1)[0] + " …"
        return {"answer": answer, "source": d["source"], "kind": d["kind"],
                "score": round(best_score, 2), "retriever": "bm25"}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    kb = KnowledgeBase.from_dir(root / "data" / "processed" / "knowledge", min_score=0)
    probes = [
        "Wie melde ich mich für Klasse B an?",
        "Gibt es Simulatoren?",
        "Welche Fördermöglichkeiten gibt es für Berufskraftfahrer?",
        "Wie sind die Öffnungszeiten am Standort Kleve?",
        "Welche Unterlagen brauche ich zur Anmeldung?",
        "Kann ich die Theorie auch online machen?",
        "Wie lange sind Sehtest und Erste-Hilfe-Kurs gültig?",
        "Welche Fahrzeuge stehen für Klasse B zur Verfügung?",
        "What are the opening hours?",
        "asdifh qwerty nonsense",
    ]
    print(f"docs indexed: {kb.N} (faq={sum(1 for d in kb.docs if d['kind']=='faq')})")
    for p in probes:
        r = kb.search(p, "de")
        print(f"\nQ: {p}\n  score={r['score'] if r else 0} src={r['source'][:60] if r else '—'}")
        if r:
            print(f"  A: {r['answer'][:130]}")
