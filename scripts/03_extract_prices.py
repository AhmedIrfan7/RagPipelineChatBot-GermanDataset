"""Step 3 / Phase 2 — deterministic price extractor for the offer-letter sheets.

The client's 44 price sheets share one exact layout (an "Angebot" / cost example):
a header, a line-item block, and a totals block. This extractor parses them with
anchored regexes — no LLM, no guessing — and then runs two arithmetic self-checks
that must both hold, or the sheet is flagged for human review:

    (A) Netto + USt-amount == Gesamtbetrag
    (B) sum(line-item Gesamtpreis) == Gesamtbetrag

Because these invariants come from the source document itself, any mis-parse (a
dropped line, a mis-read digit) breaks the equation and is caught automatically.
This is the extraction-layer half of the "never give a wrong price" guarantee.

Outputs (gitignored — contain client prices + business data):
    data/processed/prices/<variant_key>.json      one structured record per sheet
    data/processed/extraction_report.json / .md   pass/fail summary

Run:  .venv/Scripts/python.exe scripts/03_extract_prices.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pdfplumber

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw"
MANIFEST = REPO_ROOT / "data" / "interim" / "manifest.json"
OUT_DIR = REPO_ROOT / "data" / "processed" / "prices"
REPORT_JSON = REPO_ROOT / "data" / "processed" / "extraction_report.json"
REPORT_MD = REPO_ROOT / "data" / "processed" / "extraction_report.md"

CENT_TOL = 0.02  # €0.02 tolerance for float rounding in arithmetic checks

# --- line item: pos | description | anzahl | einzelpreis EUR [| USt%] | gesamt EUR
LINE_RE = re.compile(
    r"^(?:(?P<pos>\d{3}|\d{2}/\d{4})\s+)?"  # optional position code: 3-digit OR NN/NNNN; some fee lines have none
    r"(?P<desc>.+?)\s+"
    r"(?P<anzahl>\d+(?:,\d+)?)"             # quantity: integer OR German decimal (e.g. 17,67 hrs)
    r"(?:\s+(?P<unit>TN|Stk\.?|St\.?|Stück))?\s+"  # optional unit (TN = Teilnehmer)
    r"(?P<einzel>\d[\d.]*,\d{2})\s*EUR"
    r"(?:\s+(?P<ust>\d+)\s*%)?"             # USt (VAT) column optional (VAT-exempt lines omit it)
    r"\s+(?P<gesamt>\d[\d.]*,\d{2})\s*EUR\s*$"
)
NETTO_RE = re.compile(r"Summe Netto:\s*(\d[\d.]*,\d{2})\s*EUR")
UST_RE = re.compile(
    r"zzgl\.\s*(\d+)\s*%\s*USt\.(?:\s*auf\s*(\d[\d.]*,\d{2})\s*EUR)?:\s*(\d[\d.]*,\d{2})\s*EUR"
)
GESAMT_RE = re.compile(r"Gesamtbetrag:\s*(\d[\d.]*,\d{2})\s*EUR")
UEBERTRAG_RE = re.compile(r"^Übertrag:\s*(\d[\d.]*,\d{2})\s*EUR")
ZWISCHEN_RE = re.compile(r"^(Zwischensumme.+?)\s+(\d[\d.]*,\d{2})\s*EUR\s*$")
# Title line is "Kostenbeispiel <X>" or "Angebot <X>"; must NOT match "Angebot Nr. 418".
TITLE_RE = re.compile(r"^(?:Kostenbeispiel|Angebot(?!\s+Nr\.))\s+(.+?)\s*$", re.MULTILINE)
DATE_RE = re.compile(r"Datum\s+(\d{2}\.\d{2}\.\d{4})")
OFFER_RE = re.compile(r"Angebot Nr\.\s+(\d+)")
EXTERNAL_RE = re.compile(r"belaufen sich auf ca\.\s*(\d[\d.]*),-\s*€")


def de_num(s: str) -> float:
    """German '2.265,55' -> 2265.55 ; '419,00' -> 419.0."""
    return round(float(s.replace(".", "").replace(",", ".")), 2)


def load_manifest_index() -> dict:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {f["filename"]: f for f in m["files"]}


def extract_text(path: Path) -> str:
    parts = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


def parse_sheet(filename: str, meta: dict) -> dict:
    path = RAW_DIR / filename
    text = extract_text(path)
    lines = text.splitlines()

    line_items, subtotals = [], []
    for ln in lines:
        ln = ln.strip()
        m = LINE_RE.match(ln)
        if m:
            line_items.append({
                "pos": m.group("pos"),
                "description": m.group("desc").strip(),
                "anzahl": de_num(m.group("anzahl")) if "," in m.group("anzahl")
                          else int(m.group("anzahl")),
                "unit": m.group("unit"),
                "einzelpreis": de_num(m.group("einzel")),
                "ust_percent": int(m.group("ust")) if m.group("ust") else None,
                "gesamtpreis": de_num(m.group("gesamt")),
            })
            continue
        um = UEBERTRAG_RE.match(ln)
        if um:
            subtotals.append({"label": "Übertrag", "amount": de_num(um.group(1))})
            continue
        zm = ZWISCHEN_RE.match(ln)
        if zm:
            subtotals.append({"label": zm.group(1).strip(), "amount": de_num(zm.group(2))})

    netto = NETTO_RE.search(text)
    ust = UST_RE.search(text)
    gesamt = GESAMT_RE.search(text)
    title = TITLE_RE.search(text)
    date = DATE_RE.search(text)
    offer = OFFER_RE.search(text)
    ext = EXTERNAL_RE.search(text)

    totals = {
        "netto": de_num(netto.group(1)) if netto else None,
        "ust_percent": int(ust.group(1)) if ust else None,
        "ust_base": de_num(ust.group(2)) if (ust and ust.group(2)) else None,
        "ust_amount": de_num(ust.group(3)) if ust else None,
        "gesamtbetrag": de_num(gesamt.group(1)) if gesamt else None,
    }

    # --- arithmetic self-checks -------------------------------------------
    issues = []
    g = totals["gesamtbetrag"]
    n, ua = totals["netto"], totals["ust_amount"]
    check_netto_ust = None
    if None not in (g, n, ua):
        check_netto_ust = abs((n + ua) - g) <= CENT_TOL
        if not check_netto_ust:
            issues.append(f"Netto({n})+USt({ua}) != Gesamt({g})")
    line_sum = round(sum(li["gesamtpreis"] for li in line_items), 2)
    check_line_sum = None
    if g is not None:
        check_line_sum = abs(line_sum - g) <= CENT_TOL
        if not check_line_sum:
            issues.append(f"sum(line_items)={line_sum} != Gesamt({g})")
    if not line_items:
        issues.append("no line items parsed")
    if g is None:
        issues.append("no Gesamtbetrag found")

    return {
        "source_file": filename,
        "date": meta.get("date"),
        "base_class": meta.get("base_class"),
        "variant_key": meta.get("variant_key"),
        "offer_title": title.group(1).strip() if title else None,
        "offer_date": date.group(1) if date else None,
        "offer_nr": offer.group(1) if offer else None,
        "line_items": line_items,
        "subtotals": subtotals,
        "totals": totals,
        "external_fees_estimate_eur": de_num(ext.group(1) + ",00") if ext else None,
        "validation": {
            "netto_plus_ust_equals_gesamt": check_netto_ust,
            "line_items_sum_equals_gesamt": check_line_sum,
            "line_items_sum": line_sum,
            "passed": not issues,
            "issues": issues,
        },
        "raw_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index = load_manifest_index()
    sheets = [fn for fn, meta in index.items() if meta["doc_type"] == "price_sheet"]

    results, passed, flagged = [], [], []
    for fn in sorted(sheets):
        rec = parse_sheet(fn, index[fn])
        key = (rec["variant_key"] or Path(fn).stem).replace("/", "_")
        (OUT_DIR / f"{key}.json").write_text(
            json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        results.append(rec)
        (passed if rec["validation"]["passed"] else flagged).append(rec)

    report = {
        "total_sheets": len(results),
        "passed_arithmetic": len(passed),
        "flagged_for_review": len(flagged),
        "flagged": [
            {"file": r["source_file"], "gesamt": r["totals"]["gesamtbetrag"],
             "issues": r["validation"]["issues"]} for r in flagged
        ],
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md = ["# Extraction Report (LOCAL ONLY — confidential)", "",
          f"- Sheets processed: {report['total_sheets']}",
          f"- Passed both arithmetic checks: **{report['passed_arithmetic']}**",
          f"- Flagged for human review: **{report['flagged_for_review']}**", "",
          "## Flagged"]
    md += ([f"- `{f['file']}` (Gesamt={f['gesamt']}): {'; '.join(f['issues'])}"
            for f in report["flagged"]] or ["- (none — all sheets self-consistent)"])
    REPORT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"Sheets: {report['total_sheets']} | "
          f"arithmetic PASS: {report['passed_arithmetic']} | "
          f"FLAGGED: {report['flagged_for_review']}")
    for f in report["flagged"]:
        print(f"  FLAG {f['file']}: {f['issues']}")
    print(f"Wrote {len(results)} records to {OUT_DIR.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
