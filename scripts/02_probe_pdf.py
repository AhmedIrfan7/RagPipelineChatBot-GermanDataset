"""Step 3 / Phase 2 — probe the real PDF layout BEFORE writing the price extractor.

We must see how the client's price sheets are structured (text vs tables, column
layout, how euro amounts and labels sit) before committing to an extraction schema.
Guessing the layout is how price extractors silently produce wrong numbers.

Dumps, for a set of representative sheets:
    - page count
    - full extracted text
    - every detected table (as rows)
into data/interim/probe/<name>.txt  (gitignored — contains client prices).

Run:  .venv/Scripts/python.exe scripts/02_probe_pdf.py [optional_filename ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

import pdfplumber

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw"
OUT_DIR = REPO_ROOT / "data" / "interim" / "probe"

# Representative sample spanning the layout variety we care about.
DEFAULT_SAMPLES = [
    "2025_01_Klasse_B.pdf",            # the flagship car class
    "2025_01_Klasse_BE.pdf",           # trailer extension
    "2025_01_Klasse_B197.pdf",         # key-number variant
    "2025_01_Klasse_C.pdf",            # truck
    "2025_01_Klasse_C_CE_BGQ_TZ.pdf",  # complex professional-qualification sheet
    "2025_01_Klasse_A.pdf",            # motorcycle
]


def probe(filename: str) -> str:
    path = RAW_DIR / filename
    if not path.exists():
        return f"!! NOT FOUND: {filename}\n"
    lines = [f"{'='*70}", f"FILE: {filename}", f"{'='*70}"]
    with pdfplumber.open(str(path)) as pdf:
        lines.append(f"pages: {len(pdf.pages)}")
        for i, page in enumerate(pdf.pages):
            lines.append(f"\n----- PAGE {i+1} : TEXT -----")
            lines.append(page.extract_text() or "(no text)")
            tables = page.extract_tables()
            lines.append(f"\n----- PAGE {i+1} : {len(tables)} TABLE(S) -----")
            for ti, tbl in enumerate(tables):
                lines.append(f"  [table {ti+1}] {len(tbl)} rows")
                for row in tbl:
                    cells = [("" if c is None else str(c)).replace("\n", "\\n") for c in row]
                    lines.append("    | " + " | ".join(cells))
    return "\n".join(lines) + "\n"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    samples = sys.argv[1:] or DEFAULT_SAMPLES
    for fn in samples:
        report = probe(fn)
        out = OUT_DIR / (Path(fn).stem + ".txt")
        out.write_text(report, encoding="utf-8")
        # short console summary
        head = report.splitlines()
        n_tables = sum(1 for ln in head if ln.strip().startswith("[table"))
        pages = next((ln for ln in head if ln.startswith("pages:")), "pages: ?")
        print(f"probed {fn}: {pages}, tables={n_tables} -> {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
