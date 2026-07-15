"""n8n data prep, step 2: recategorize the client's knowledge base into the 5-category
taxonomy (Pricing, Location & Hours, Registration & Documents, Courses & Offerings,
General) and split multi-topic documents by subtopic instead of embedding them whole.

Fixes a real gap found while planning: beratungsgespraech_b.txt was extracted by
scripts/05_extract_knowledge.py but never made it into rag_chunks.json, so it was
invisible to retrieval in the existing Python system too. This script includes it.

Output is confidential (real client text) -> n8n/data/ is gitignored.

Run:  .venv/Scripts/python.exe n8n/scripts/02_prepare_knowledge_chunks.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KDIR = REPO_ROOT / "data" / "processed" / "knowledge"
OUT_DIR = REPO_ROOT / "n8n" / "data"

CATEGORIES = ["pricing", "location_hours", "registration_docs", "courses_offerings", "general"]

# section_title -> category, for the plain (non-special-cased) Informationsbogen sections
SECTION_CATEGORY = {
    "Standorte": "location_hours",
    "Angebote und Kurse": "courses_offerings",
    "Terminvereinbarung und Anmeldung": "registration_docs",
    "Kurswahl und Vertragsabschluss": "registration_docs",
    "Ausbildungsbeginn": "registration_docs",
    "Prüfungsanmeldung": "registration_docs",
    "Preise und Finanzierung": "pricing",
    "Richtlinien und Rechtliches": "general",
    "Verkehrsregelungen an Bahnübergängen": "courses_offerings",
    "Verkehrsverhalten bei Fahrmanövern": "courses_offerings",
    "Abfahrtskontrolle:": "courses_offerings",
    "Fahrprüfung selbst:": "courses_offerings",
    "Bewertung und Abschluss:": "courses_offerings",
}
# excluded entirely: meta / example-query answer key / testimonials, not answerable content
EXCLUDED_SECTIONS = {"Einleitung", "Ressourcen & Anhänge"}
# handled specially (split into sub-chunks / cross-tagged): "Unternehmensübersicht und
# Hintergrund", "Personal & Team", "Häufige Fragen (FAQs)"

# FAQ pairs are numbered 1-14 in source order; classify each individually rather than
# dumping the whole FAQ section into one bucket, since they span multiple topics.
FAQ_CATEGORY_BY_INDEX = {
    1: "registration_docs", 2: "registration_docs", 3: "registration_docs",
    4: "registration_docs", 5: "registration_docs",
    6: "courses_offerings", 7: "courses_offerings",
    8: "pricing", 9: "pricing",
    10: "courses_offerings", 11: "courses_offerings",
    12: "registration_docs", 13: "registration_docs",
    14: "courses_offerings",
}

AZAV_MARKER = "AZAV-Zertifizierung"
HI_FIVE_MARKER = "„Hi Five“-Empfehlungsaktion"
ERREICHBARKEIT_MARKER = "Erreichbarkeit:"


def add(chunks: list, category: str, source: str, text: str, **meta) -> None:
    if not text.strip():
        return
    chunks.append({"category": category, "source": source, "text": text.strip(), "metadata": meta})


def split_faqs(section_text: str) -> list[tuple[int, str, str]]:
    """Return [(index, question, answer), ...] from the FAQ section text."""
    lines = section_text.splitlines()
    q_re = re.compile(r"^\s*(\d+)\.\s*(.+\?)\s*$")
    out, i = [], 0
    while i < len(lines):
        m = q_re.match(lines[i].strip())
        if m:
            idx, q = int(m.group(1)), m.group(2).strip()
            ans, j = [], i + 1
            while j < len(lines) and not q_re.match(lines[j].strip()):
                if lines[j].strip():
                    ans.append(lines[j].strip())
                j += 1
            out.append((idx, q, " ".join(ans)))
            i = j
        else:
            i += 1
    return out


def process_informationsbogen(chunks: list) -> None:
    sections = json.loads((KDIR / "informationsbogen_sections.json").read_text(encoding="utf-8"))
    for s in sections:
        title, text = s["title"], s["text"]
        if title in EXCLUDED_SECTIONS:
            continue

        if title == "Häufige Fragen (FAQs)":
            for idx, q, a in split_faqs(text):
                cat = FAQ_CATEGORY_BY_INDEX.get(idx, "general")
                add(chunks, cat, q, f"{q}\n{a}", faq_index=idx)
            continue

        if title == "Unternehmensübersicht und Hintergrund":
            add(chunks, "general", title, text)
            if AZAV_MARKER in text:
                start = text.index(AZAV_MARKER)
                end = text.find("Zielgruppen:", start)
                add(chunks, "pricing", "Förderung (AZAV/Bildungsgutschein)",
                    text[start:end if end != -1 else None], cross_tag=True)
            continue

        if title == "Angebote und Kurse":
            add(chunks, "courses_offerings", title, text)
            if HI_FIVE_MARKER in text:
                start = text.index(HI_FIVE_MARKER)
                end = text.find("Insgesamt 8 Fahrlehrer", start)
                add(chunks, "pricing", "Hi Five Empfehlungsaktion",
                    text[start:end if end != -1 else None], cross_tag=True)
            continue

        if title == "Personal & Team":
            add(chunks, "general", title, text)
            if ERREICHBARKEIT_MARKER in text:
                start = text.index(ERREICHBARKEIT_MARKER)
                add(chunks, "location_hours", "Erreichbarkeit (Team)", text[start:], cross_tag=True)
            continue

        cat = SECTION_CATEGORY.get(title)
        if cat:
            add(chunks, cat, title, text)
        else:
            add(chunks, "general", title, text)  # unmapped section -> safe default


def process_beratungsprotokoll_b(chunks: list) -> None:
    text = (KDIR / "beratungsprotokoll_b.txt").read_text(encoding="utf-8")
    marker = "Prüfungskosten werden von der Fahrschule UND vom TÜV"
    if marker in text:
        idx = text.index(marker)
        add(chunks, "courses_offerings", "Beratungsprotokoll Klasse B (Ablauf)",
            text[:idx], base_class="B")
        add(chunks, "pricing", "Beratungsprotokoll Klasse B (Kosten)",
            text[idx:], base_class="B")
    else:
        add(chunks, "courses_offerings", "Beratungsprotokoll Klasse B", text, base_class="B")


def process_beratungsgespraech_b(chunks: list) -> None:
    path = KDIR / "beratungsgespraech_b.txt"
    if not path.exists():
        return  # source PDF wasn't available in this environment
    text = path.read_text(encoding="utf-8")

    def between(a: str, b: str | None) -> str:
        if a not in text:
            return ""
        start = text.index(a)
        end = text.index(b, start) if b and b in text else None
        return text[start:end]

    add(chunks, "courses_offerings", "Beratungsgespräch B (Theorie/Praxis/Simulator)",
        between("Theoretische Ausbildung", "Dokumente"), base_class="B")
    add(chunks, "registration_docs", "Beratungsgespräch B (Dokumente)",
        between("Dokumente\n\nALLE DOKUMENTE", "Kosten"), base_class="B")
    add(chunks, "pricing", "Beratungsgespräch B (Kosten)",
        between("Kosten\n\nFAHRSCHULE", "Noch Fragen?"), base_class="B")
    add(chunks, "location_hours", "Beratungsgespräch B (Kontakt)",
        between("Noch Fragen?", None), base_class="B")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    chunks: list = []
    process_informationsbogen(chunks)
    process_beratungsprotokoll_b(chunks)
    process_beratungsgespraech_b(chunks)

    by_cat: dict[str, list] = {c: [] for c in CATEGORIES}
    for c in chunks:
        by_cat[c["category"]].append(c)

    for cat, items in by_cat.items():
        out_path = OUT_DIR / f"{cat}.json"
        out_path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{cat:20} {len(items):3} chunks -> {out_path.relative_to(REPO_ROOT)}")

    cross_tags = sum(1 for c in chunks if c["metadata"].get("cross_tag"))
    print(f"\ntotal chunks: {len(chunks)} (including {cross_tags} deliberate cross-tags)")


if __name__ == "__main__":
    main()
