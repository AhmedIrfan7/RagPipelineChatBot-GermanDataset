"""GOLDEN price regression gate — the accuracy contract.

Runs against the REAL verified records (confidential, local only). Each expected
value below was read directly from the source PDF during extraction. If any price
ever changes unexpectedly, or a sheet stops satisfying its arithmetic invariants,
this test fails and blocks the change.

Skipped automatically when the real data is not present (e.g. on public CI).
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fahrschule.store import Store
from fahrschule.disambiguation import TREES, Disambiguator

PRICES_DIR = ROOT / "data" / "processed" / "prices"
MANIFEST = ROOT / "data" / "interim" / "manifest.json"

# variant_key -> exact expected Gesamtbetrag (ground truth from source PDFs)
GOLDEN = {
    "B": 2696.00,
    "BE": 894.00,
    "B197": 2696.21,
    "Mofa": 236.96,
    "CE_BA": 3372.06,
    "C": 2461.00,
    "A": 1866.00,
    "L": 577.00,
    "C_CE_BGQ_TZ": 6608.00,
}

_have_data = PRICES_DIR.exists() and any(PRICES_DIR.glob("*.json"))


@unittest.skipUnless(_have_data, "real client data not present (local-only)")
class TestGoldenPrices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store.from_dir(PRICES_DIR, MANIFEST)

    def test_golden_totals(self):
        for key, expected in GOLDEN.items():
            rec = self.store.get_price(key)
            self.assertIsNotNone(rec, f"{key} not found or not current")
            self.assertEqual(rec["totals"]["gesamtbetrag"], expected,
                             f"{key}: expected {expected}, got {rec['totals']['gesamtbetrag']}")

    def test_every_current_sheet_arithmetic_holds(self):
        """Independently re-derive both invariants for every current sheet."""
        for key, rec in self.store.by_key.items():
            if not rec["is_current"]:
                continue
            t = rec["totals"]
            g = t["gesamtbetrag"]
            self.assertIsNotNone(g, f"{key}: no Gesamtbetrag")
            line_sum = round(sum(li["gesamtpreis"] for li in rec["line_items"]), 2)
            self.assertLessEqual(abs(line_sum - g), 0.02,
                                 f"{key}: sum(lines)={line_sum} != Gesamt={g}")
            if t.get("netto") is not None and t.get("ust_amount") is not None:
                self.assertLessEqual(abs(t["netto"] + t["ust_amount"] - g), 0.02,
                                     f"{key}: netto+ust != Gesamt")

    def test_older_generation_is_archived(self):
        """2024_06 sheets must not be served by default."""
        for key, rec in self.store.by_key.items():
            if rec.get("date") == "2024_06":
                self.assertIsNone(self.store.get_price(key),
                                  f"{key}: archived sheet served by default!")
                self.assertIsNotNone(self.store.get_price(key, allow_archived=True))

    def test_klasse_b_triggers_disambiguation(self):
        r = self.store.resolve_class("Was kostet der Führerschein Klasse B?")
        self.assertEqual(r.status, "ambiguous")
        self.assertIn("B", r.candidates)       # plain B is one option, not THE answer
        self.assertIn("B197", r.candidates)
        self.assertGreater(len(r.candidates), 5, "Class B should surface many variants")

    def test_disambiguation_trees_cover_current_variants_exactly(self):
        """Every tree leaf must be a current variant, and every current variant of a
        treed base must be reachable — otherwise a price is unreachable or a leaf is
        dead. This closes the loop between the store and the follow-up logic."""
        d = Disambiguator()
        for base in TREES:
            leaves = d.leaf_variants(base)
            current = {v["variant_key"] for v in self.store.list_variants(base)}
            self.assertEqual(leaves, current,
                             f"base {base}: tree leaves {leaves ^ current} mismatch store")

    def test_walk_resolves_to_real_price(self):
        """An end-to-end walk (B, automatic) must land on a variant the store can price."""
        d = Disambiguator()
        d.choose("b_situation", "neu")
        d.choose("b_combine", "nur_b")
        step = d.choose("b_transmission", "automatik")
        rec = self.store.get_price(step.variant_key)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["totals"]["gesamtbetrag"], 2696.21)  # B197


if __name__ == "__main__":
    unittest.main()
