"""Ground-truth price gate: every served price must equal the Gesamtbetrag parsed
INDEPENDENTLY from the source PDF (a second parser, not the extraction pipeline).

This is the strongest correctness guarantee — it catches any divergence between what
the bot serves and what the official price sheet actually says, not merely internal
consistency. Data-gated (needs the raw PDFs + pdfplumber), so it skips on public CI.
"""

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

RAW = ROOT / "data" / "raw"
MANIFEST = ROOT / "data" / "interim" / "manifest.json"
PRICES = ROOT / "data" / "processed" / "prices"

_have = RAW.exists() and MANIFEST.exists() and PRICES.exists() and any(PRICES.glob("*.json"))
try:
    import pdfplumber  # noqa: F401
    _pdf = True
except Exception:
    _pdf = False

_GESAMT = re.compile(r"Gesamtbetrag:\s*([\d.]+,\d{2})\s*EUR")


def _de_num(s: str) -> float:
    return round(float(s.replace(".", "").replace(",", ".")), 2)


@unittest.skipUnless(_have and _pdf, "raw PDFs / pdfplumber not present (local-only)")
class TestPriceVsPdf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fahrschule.store import Store
        cls.store = Store.from_dir(PRICES, MANIFEST)
        cls.files = [f for f in json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
                     if f["doc_type"] == "price_sheet"]

    def test_every_served_price_matches_source_pdf(self):
        import pdfplumber
        mismatches = []
        for f in self.files:
            vk = f["variant_key"]
            with pdfplumber.open(str(RAW / f["filename"])) as pdf:
                text = "\n".join(pg.extract_text() or "" for pg in pdf.pages)
            found = _GESAMT.findall(text)
            self.assertTrue(found, f"{f['filename']}: no Gesamtbetrag in PDF")
            pdf_total = _de_num(found[-1])
            served = self.store.by_key[vk]["totals"]["gesamtbetrag"]
            if served != pdf_total:
                mismatches.append(f"{vk}: served {served} != PDF {pdf_total}")
        self.assertEqual(mismatches, [], f"price/PDF mismatches: {mismatches}")


if __name__ == "__main__":
    unittest.main()
