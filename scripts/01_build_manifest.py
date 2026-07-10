"""Step 2 / Phase 1 — build the dataset manifest, taxonomy cross-reference,
and version-conflict map from the client PDFs in ``data/raw/``.

Pure standard library (no third-party deps) so it is robust on any Python 3.11+.

Outputs (written to the gitignored ``data/interim/`` — they describe which files
the client has, so they stay local, never public):
    - data/interim/manifest.json
    - data/interim/manifest.md

Also prints a human-readable audit + version-conflict report to stdout, which is
the artifact we validate against the raw filenames.

Run:  python scripts/01_build_manifest.py
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# --- make src/ importable ---
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from fahrschule.taxonomy import (  # noqa: E402
    DOC_TYPE_TOKENS,
    VARIANTS,
    parse_price_token,
    token_confidence,
)


def _nfc(s: str) -> str:
    """Normalize to composed Unicode so decomposed umlauts (a+U+0308) match."""
    return unicodedata.normalize("NFC", s)

RAW_DIR = REPO_ROOT / "data" / "raw"
OUT_DIR = REPO_ROOT / "data" / "interim"

DATE_RE = re.compile(r"^(\d{4})_(\d{2})_(.+)$")
PRICE_PREFIX_RE = re.compile(r"^(Klasse|Kl)_(.+)$")

# Tokens that are course-format / funding / key-number markers, not class codes.
NON_CLASS_TOKENS = set(VARIANTS.keys()) | {"BA", "TZ", "VZ", "BGQ", "S"}


def classify(filename: str) -> dict:
    """Parse one filename into structured metadata."""
    fname = _nfc(filename)
    stem = fname[:-4] if fname.lower().endswith(".pdf") else fname
    entry: dict = {
        "filename": filename,           # keep original for disk lookups
        "date": None,
        "year": None,
        "month": None,
        "doc_type": "unknown",
        "base_class": None,
        "variant_key": None,
        "variant_tokens": [],
        "low_confidence_tokens": [],
    }

    date_m = DATE_RE.match(stem)
    if date_m:
        entry["year"], entry["month"] = int(date_m.group(1)), int(date_m.group(2))
        entry["date"] = f"{date_m.group(1)}_{date_m.group(2)}"

    # Special document types (business info / consultation / price display) --
    for token, dtype in DOC_TYPE_TOKENS.items():
        if _nfc(token).lower() in stem.lower():
            entry["doc_type"] = dtype
            if dtype == "price_display":
                entry["base_class"] = "C"          # Drive-In Lkw = truck display
                entry["variant_key"] = "Preisaushang_Drive_In_Lkw"
            elif dtype in {"consultation_protocol", "consultation_dialog"} and \
                    re.search(r"(^|[_ ])B([_ ]|$)", stem):
                entry["base_class"] = "B"
            return entry

    # Dated price sheet ------------------------------------------------------
    if date_m:
        pm = PRICE_PREFIX_RE.match(date_m.group(3))
        if pm:
            variant_key = pm.group(2).strip("_")
            parsed = parse_price_token(variant_key)
            entry["doc_type"] = "price_sheet"
            entry["variant_key"] = variant_key
            entry["variant_tokens"] = parsed["sub_tokens"]
            entry["base_class"] = parsed["base_class"]
            entry["low_confidence_tokens"] = [
                t for t in parsed["sub_tokens"]
                if token_confidence(t) == "needs_verification"
            ]
    return entry


def build() -> dict:
    if not RAW_DIR.exists():
        sys.exit(f"ERROR: {RAW_DIR} not found. Place client PDFs there first.")
    pdfs = sorted(RAW_DIR.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"ERROR: no PDFs in {RAW_DIR}.")

    files = []
    for p in pdfs:
        e = classify(p.name)
        e["size_bytes"] = p.stat().st_size
        files.append(e)

    price_sheets = [f for f in files if f["doc_type"] in ("price_sheet", "price_display")]
    dated = [f for f in price_sheets if f["date"]]
    newest = max((f["date"] for f in dated), default=None)

    # mark currency + supersession candidates
    for f in price_sheets:
        f["is_current"] = (f["date"] == newest)
        f["supersession"] = None
        if f["date"] and f["date"] != newest:
            # candidate successors: newest-gen sheets sharing >=2 class tokens
            my_tokens = {t for t in (f["variant_tokens"] or []) if t not in NON_CLASS_TOKENS}
            cands = []
            for g in dated:
                if g["date"] != newest:
                    continue
                g_tokens = {t for t in (g["variant_tokens"] or []) if t not in NON_CLASS_TOKENS}
                if len(my_tokens & g_tokens) >= 2 or (my_tokens and my_tokens <= g_tokens):
                    cands.append(g["filename"])
            f["supersession"] = {
                "status": "older_generation_needs_human_review",
                "candidate_successors": cands,
            }

    # exact same variant_key across different dates = hard conflict
    by_key: dict[str, list] = {}
    for f in price_sheets:
        if f["variant_key"]:
            by_key.setdefault(f["variant_key"], []).append(f)
    conflicts = [
        {"variant_key": k, "files": [x["filename"] for x in v],
         "dates": sorted({x["date"] for x in v})}
        for k, v in by_key.items()
        if len({x["date"] for x in v if x["date"]}) > 1
    ]

    by_base: dict[str, int] = {}
    for f in price_sheets:
        by_base[f["base_class"] or "?"] = by_base.get(f["base_class"] or "?", 0) + 1

    uncertain = sorted({t for f in files for t in f["low_confidence_tokens"]})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": "data/raw",
        "file_count": len(files),
        "newest_generation": newest,
        "counts_by_doc_type": _counts(files, "doc_type"),
        "counts_by_base_class": by_base,
        "uncertain_tokens_needing_client_confirmation": uncertain,
        "hard_version_conflicts": conflicts,
        "older_generation_files": [
            {"filename": f["filename"], "date": f["date"], **f["supersession"]}
            for f in price_sheets if f.get("supersession")
        ],
        "files": files,
    }


def _counts(items, key):
    out: dict[str, int] = {}
    for it in items:
        out[it[key]] = out.get(it[key], 0) + 1
    return out


def write_markdown(m: dict, path: Path) -> None:
    lines = [
        "# Dataset Manifest (LOCAL ONLY — confidential)",
        "",
        f"- Generated: {m['generated_at']}",
        f"- Files: {m['file_count']}",
        f"- Newest price generation: **{m['newest_generation']}**",
        "",
        "## By document type",
        *[f"- {k}: {v}" for k, v in sorted(m["counts_by_doc_type"].items())],
        "",
        "## Price sheets by base class",
        *[f"- {k}: {v}" for k, v in sorted(m["counts_by_base_class"].items())],
        "",
        "## Tokens needing client confirmation",
        *([f"- `{t}`" for t in m["uncertain_tokens_needing_client_confirmation"]] or ["- (none)"]),
        "",
        "## Older-generation files (verify supersession before archiving)",
    ]
    for f in m["older_generation_files"]:
        succ = ", ".join(f["candidate_successors"]) or "NONE FOUND — flag to client"
        lines.append(f"- `{f['filename']}` ({f['date']}) → candidates: {succ}")
    lines += ["", "## Hard version conflicts (identical variant key, multiple dates)"]
    if m["hard_version_conflicts"]:
        for c in m["hard_version_conflicts"]:
            lines.append(f"- `{c['variant_key']}`: {c['files']} across {c['dates']}")
    else:
        lines.append("- (none)")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows console is cp1252
    except Exception:
        pass
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    m = build()
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(m, OUT_DIR / "manifest.md")

    # stdout report (validation surface) -----------------------------------
    print("=" * 68)
    print(f"MANIFEST: {m['file_count']} files | newest generation {m['newest_generation']}")
    print("=" * 68)
    print("Doc types:", m["counts_by_doc_type"])
    print("Price sheets by base class:", m["counts_by_base_class"])
    print("Uncertain tokens (client must confirm):",
          m["uncertain_tokens_needing_client_confirmation"])
    print(f"Older-generation files: {len(m['older_generation_files'])}")
    for f in m["older_generation_files"]:
        print(f"  - {f['filename']} -> {f['candidate_successors'] or 'NO CANDIDATE'}")
    print(f"Hard version conflicts: {len(m['hard_version_conflicts'])}")
    for c in m["hard_version_conflicts"]:
        print(f"  - {c['variant_key']}: {c['dates']}")
    print(f"\nWrote: {OUT_DIR / 'manifest.json'}")
    print(f"Wrote: {OUT_DIR / 'manifest.md'}")


if __name__ == "__main__":
    main()
