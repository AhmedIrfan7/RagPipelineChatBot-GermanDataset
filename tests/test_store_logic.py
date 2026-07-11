"""Store query-layer logic tests using SYNTHETIC data (fake prices) so the routing
and archiving logic is verified without shipping real client prices. Safe anywhere."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fahrschule.store import Store


def synthetic_store() -> Store:
    recs = [
        {"variant_key": "B", "base_class": "B", "date": "2025_01",
         "offer_title": "Kl. B", "source_file": "b.pdf",
         "totals": {"gesamtbetrag": 100.0}, "line_items": []},
        {"variant_key": "B197", "base_class": "B", "date": "2025_01",
         "offer_title": "Kl. B197", "source_file": "b197.pdf",
         "totals": {"gesamtbetrag": 150.0}, "line_items": []},
        {"variant_key": "BE", "base_class": "BE", "date": "2025_01",
         "offer_title": "Kl. BE", "source_file": "be.pdf",
         "totals": {"gesamtbetrag": 200.0}, "line_items": []},
        {"variant_key": "BE_T", "base_class": "BE", "date": "2025_01",
         "offer_title": "Kl. BE_T", "source_file": "bet.pdf",
         "totals": {"gesamtbetrag": 250.0}, "line_items": []},
        {"variant_key": "AM", "base_class": "AM", "date": "2025_01",
         "offer_title": "Kl. AM", "source_file": "am.pdf",
         "totals": {"gesamtbetrag": 300.0}, "line_items": []},
        {"variant_key": "OLD", "base_class": "B", "date": "2024_06",
         "offer_title": "old", "source_file": "old.pdf",
         "totals": {"gesamtbetrag": 999.0}, "line_items": []},
    ]
    return Store(recs)


class TestLookups(unittest.TestCase):
    def setUp(self):
        self.s = synthetic_store()

    def test_get_price_current(self):
        self.assertEqual(self.s.get_price("B")["totals"]["gesamtbetrag"], 100.0)

    def test_archived_withheld_by_default(self):
        self.assertIsNone(self.s.get_price("OLD"))
        self.assertEqual(self.s.get_price("OLD", allow_archived=True)["totals"]["gesamtbetrag"], 999.0)

    def test_unknown_key(self):
        self.assertIsNone(self.s.get_price("ZZZ"))

    def test_list_variants_excludes_archived(self):
        keys = {v["variant_key"] for v in self.s.list_variants("B")}
        self.assertEqual(keys, {"B", "B197"})
        keys_all = {v["variant_key"] for v in self.s.list_variants("B", current_only=False)}
        self.assertEqual(keys_all, {"B", "B197", "OLD"})

    def test_document_link(self):
        self.assertEqual(self.s.get_document_link("B"), "b.pdf")


class TestResolveClass(unittest.TestCase):
    def setUp(self):
        self.s = synthetic_store()

    def test_bare_base_is_ambiguous(self):
        r = self.s.resolve_class("Was kostet Klasse B?")
        self.assertEqual(r.status, "ambiguous")
        self.assertTrue({"B", "B197"} <= set(r.candidates))

    def test_specific_keynumber_resolves(self):
        r = self.s.resolve_class("Ich brauche B197")
        self.assertEqual(r.status, "resolved")
        self.assertEqual(r.variant_key, "B197")

    def test_be_ambiguous(self):
        r = self.s.resolve_class("BE")
        self.assertEqual(r.status, "ambiguous")
        self.assertEqual(set(r.candidates), {"BE", "BE_T"})

    def test_single_variant_base_resolves(self):
        r = self.s.resolve_class("AM bitte")
        self.assertEqual(r.status, "resolved")
        self.assertEqual(r.variant_key, "AM")

    def test_synonym_maps_to_base(self):
        r = self.s.resolve_class("Preis für Auto Führerschein")
        self.assertEqual(r.status, "ambiguous")
        self.assertTrue({"B", "B197"} <= set(r.candidates))

    def test_not_found(self):
        self.assertEqual(self.s.resolve_class("Segelschein für ein Boot").status, "not_found")


if __name__ == "__main__":
    unittest.main()
