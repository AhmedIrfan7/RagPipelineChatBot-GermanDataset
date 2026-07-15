"""n8n data prep, step 1: convert the already-verified price records into clean
natural-language text chunks ready for embedding.

Reuses fahrschule.store.Store (the same verified, arithmetic-checked records the
Python chatbot serves) instead of re-parsing raw PDFs. Only CURRENT records are
included, matching the existing "never serve an archived price" rule, carried into
the RAG pipeline as well even though this path no longer enforces it deterministically.

Output is confidential (real client prices) -> n8n/data/ is gitignored.

Run:  .venv/Scripts/python.exe n8n/scripts/01_prepare_pricing_chunks.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from fahrschule.store import Store  # noqa: E402

PRICES_DIR = REPO_ROOT / "data" / "processed" / "prices"
MANIFEST = REPO_ROOT / "data" / "interim" / "manifest.json"
OUT_DIR = REPO_ROOT / "n8n" / "data"


def chunk_text(rec: dict) -> str:
    """Render one verified price record as clean prose, the way a person would
    describe the sheet out loud. This is what gets embedded, not the raw PDF text."""
    t = rec["totals"]
    lines = [
        f"Kostenbeispiel: {rec.get('offer_title') or rec['variant_key']} "
        f"(Klasse {rec['base_class']}, Variante {rec['variant_key']}), "
        f"Preisstand {rec.get('offer_date') or rec['date']}.",
        "",
        "Einzelposten:",
    ]
    for li in rec["line_items"]:
        unit = f" {li['unit']}" if li.get("unit") else ""
        vat = f", {li['ust_percent']}% USt." if li.get("ust_percent") else ""
        lines.append(
            f"- {li['description']}: {li['anzahl']}{unit} x "
            f"{li['einzelpreis']:.2f} EUR = {li['gesamtpreis']:.2f} EUR{vat}"
        )
    lines.append("")
    if t.get("netto") is not None:
        lines.append(f"Nettobetrag: {t['netto']:.2f} EUR")
    if t.get("ust_amount") is not None:
        lines.append(f"USt. ({t.get('ust_percent', 19)}%): {t['ust_amount']:.2f} EUR")
    lines.append(f"Gesamtbetrag: {t['gesamtbetrag']:.2f} EUR")
    if rec.get("external_fees_estimate_eur"):
        lines.append(
            f"Zusätzlich externe Gebühren (TÜV, Bürgerbüro, Sehtest, Erste-Hilfe, "
            f"nicht von der Fahrschule berechnet): ca. {rec['external_fees_estimate_eur']:.0f} EUR."
        )
    return "\n".join(lines)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    store = Store.from_dir(PRICES_DIR, MANIFEST)

    chunks = []
    for key, rec in sorted(store.by_key.items()):
        if not rec["is_current"]:
            continue  # archived generations never enter the RAG corpus
        chunks.append({
            "category": "pricing",
            "source": rec.get("offer_title") or key,
            "text": chunk_text(rec),
            "metadata": {
                "variant_key": key,
                "base_class": rec["base_class"],
                "date": rec["date"],
                "source_file": rec["source_file"],
                "gesamtbetrag": rec["totals"]["gesamtbetrag"],
            },
        })

    out_path = OUT_DIR / "pricing_chunks.json"
    out_path.write_text(json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(chunks)} pricing chunks -> {out_path.relative_to(REPO_ROOT)}")
    skipped = len(store.by_key) - len(chunks)
    print(f"skipped {skipped} archived record(s)")


if __name__ == "__main__":
    main()
