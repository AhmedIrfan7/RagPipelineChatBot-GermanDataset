"""Step 5 / Phase 2 — extract the non-price documents into a clean knowledge base.

Covers the three descriptive documents:
    - Informationsbogen (17 pp): the questionnaire the client answered specifically
      to power this assistant — company info, offerings, registration, exam process,
      FAQs, policies. This is the primary source for general (non-price) questions.
    - Beratungsprotokoll B: the Class-B consultation checklist (theory schedule,
      Sonderfahrten counts, simulator, external fees, exam costs).
    - Beratungsgespräch B: Class-B consultation deck (may be image-only -> flagged).

Produces (gitignored data/processed/knowledge/ — confidential business content):
    - <slug>.txt                     full extracted text per document
    - informationsbogen_sections.json  top-level sections for RAG chunking
    - rag_chunks.json                  section-level chunks with metadata (DE)

Run:  .venv/Scripts/python.exe scripts/05_extract_knowledge.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pdfplumber
from pdfminer.high_level import extract_text

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw"
OUT_DIR = REPO_ROOT / "data" / "processed" / "knowledge"

# match documents by substring (robust against unicode/dashes in filenames)
DOCS = {
    "informationsbogen": "Informationsbogen",
    "beratungsprotokoll_b": "Beratungsprotokoll",
    "beratungsgespraech_b": "Beratungsgespr",   # umlaut-safe prefix
}

# a top-level section header: "N. Title" (single digit 1-9, short, no question mark)
SECTION_RE = re.compile(r"^([1-9])\.\s+([A-ZÄÖÜ][^?\n]{2,48})$")


def find_doc(substr: str) -> Path | None:
    for p in RAW_DIR.glob("*.pdf"):
        if substr.lower() in p.name.lower():
            return p
    return None


def full_text(path: Path) -> str:
    """Prefer pdfminer (better on these letters); fall back to pdfplumber."""
    try:
        t = extract_text(str(path)) or ""
    except Exception:
        t = ""
    if len(t.strip()) < 50:
        try:
            with pdfplumber.open(str(path)) as pdf:
                t = "\n".join(pg.extract_text() or "" for pg in pdf.pages)
        except Exception:
            t = ""
    return t


def segment_sections(text: str) -> list[dict]:
    sections: list[dict] = []
    current = {"section_no": 0, "title": "Einleitung", "lines": []}
    for raw in text.splitlines():
        line = raw.strip()
        m = SECTION_RE.match(line)
        if m:
            if current["lines"]:
                sections.append(current)
            current = {"section_no": int(m.group(1)), "title": m.group(2).strip(), "lines": []}
        elif line:
            current["lines"].append(line)
    if current["lines"]:
        sections.append(current)
    for s in sections:
        s["text"] = "\n".join(s.pop("lines"))
    return sections


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    texts: dict[str, str] = {}
    for slug, substr in DOCS.items():
        path = find_doc(substr)
        if not path:
            print(f"  [skip] {slug}: file not found")
            continue
        t = full_text(path)
        texts[slug] = t
        (OUT_DIR / f"{slug}.txt").write_text(t, encoding="utf-8")
        status = "OK" if len(t.strip()) >= 50 else "EMPTY (image-only? needs OCR)"
        print(f"  {slug}: {len(t)} chars from '{path.name}'  -> {status}")

    # segment the Informationsbogen
    chunks: list[dict] = []
    if texts.get("informationsbogen", "").strip():
        sections = segment_sections(texts["informationsbogen"])
        (OUT_DIR / "informationsbogen_sections.json").write_text(
            json.dumps(sections, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nInformationsbogen sections: {len(sections)}")
        for s in sections:
            print(f"  [{s['section_no']}] {s['title']} ({len(s['text'])} chars)")
            chunks.append({
                "doc": "informationsbogen", "lang": "de",
                "section_no": s["section_no"], "section_title": s["title"],
                "text": s["text"],
            })

    # the Beratungsprotokoll B is short -> one chunk (Class-B specific)
    if texts.get("beratungsprotokoll_b", "").strip():
        chunks.append({
            "doc": "beratungsprotokoll_b", "lang": "de",
            "section_no": None, "section_title": "Beratungsprotokoll Klasse B",
            "base_class": "B", "text": texts["beratungsprotokoll_b"],
        })

    (OUT_DIR / "rag_chunks.json").write_text(
        json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nRAG chunks written: {len(chunks)} -> {OUT_DIR.relative_to(REPO_ROOT)}/rag_chunks.json")


if __name__ == "__main__":
    main()
