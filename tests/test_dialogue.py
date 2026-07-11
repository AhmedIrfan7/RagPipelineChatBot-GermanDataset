"""Dialogue engine tests. Pure language-detection tests run anywhere; the full
conversation flows use the REAL store (skipped when local data is absent)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fahrschule.dialogue import DialogueEngine, Session, detect_language
from fahrschule.disambiguation import Disambiguator
from fahrschule.store import Store

PRICES_DIR = ROOT / "data" / "processed" / "prices"
MANIFEST = ROOT / "data" / "interim" / "manifest.json"
_have_data = PRICES_DIR.exists() and any(PRICES_DIR.glob("*.json"))


class TestLanguageDetect(unittest.TestCase):
    def test_german(self):
        self.assertEqual(detect_language("Was kostet die Klasse B?"), "de")

    def test_english(self):
        self.assertEqual(detect_language("How much does class B cost?"), "en")


@unittest.skipUnless(_have_data, "real client data not present (local-only)")
class TestConversation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store.from_dir(PRICES_DIR, MANIFEST)
        cls.eng = DialogueEngine(cls.store, Disambiguator(), handoff_email="team@example.com")

    def _b_walk(self, session):
        self.eng.handle_text(session, "Was kostet Klasse B?")
        self.eng.handle_option(session, "neu")
        self.eng.handle_option(session, "nur_b")
        self.eng.handle_option(session, "manuell")
        return self.eng.handle_option(session, "standard")

    def test_klasse_b_starts_disambiguation(self):
        s = Session("t1")
        r = self.eng.handle_text(s, "Was kostet Klasse B?")
        self.assertEqual(r.kind, "question")
        self.assertEqual(s.stage, "disambiguating")
        self.assertEqual(s.node_id, "b_situation")
        self.assertTrue(any(o["key"] == "neu" for o in r.options))

    def test_full_b_walk_returns_exact_price(self):
        s = Session("t2")
        r = self._b_walk(s)
        self.assertEqual(r.kind, "price")
        self.assertEqual(r.price["variant_key"], "B")
        self.assertEqual(r.price["gesamtbetrag"], 2696.0)
        self.assertIsNotNone(r.document)          # PDF delivered
        self.assertIn("250", r.text)              # external fees mentioned
        self.assertIn("Download", r.text)

    def test_resolved_single_variant(self):
        s = Session("t3")
        r = self.eng.handle_text(s, "Preis Klasse AM")
        self.assertEqual(r.kind, "price")
        self.assertEqual(r.price["variant_key"], "AM")
        self.assertEqual(r.price["gesamtbetrag"], 1456.0)

    def test_unresolvable_hands_off(self):
        s = Session("t4")
        r = self.eng.handle_text(s, "Was kostet ein Segelboot?")
        self.assertEqual(r.kind, "handoff")
        self.assertIn("team@example.com", r.text)

    def test_english_walk_is_in_english(self):
        s = Session("t5")
        r = self.eng.handle_text(s, "What does class B cost?")
        self.assertEqual(s.language, "en")
        self.assertEqual(r.kind, "question")
        self.assertIn("new license", r.text.lower())

    def test_needs_verification_caveat(self):
        s = Session("t6")
        self.eng.handle_text(s, "Klasse A2")           # base A2 -> tree
        self.assertEqual(s.node_id, "a2_root")
        r = self.eng.handle_option(s, "a2s")           # A2S (needs_verification)
        self.assertEqual(r.kind, "price")
        self.assertEqual(r.price["gesamtbetrag"], 728.0)
        self.assertIn("Sonderform", r.text)

    def test_cross_base_synonym_asks_which_class(self):
        s = Session("t7")
        r = self.eng.handle_text(s, "Ich möchte ein Motorrad fahren")
        self.assertEqual(r.kind, "question")
        self.assertEqual(s.stage, "choose_base")
        keys = {o["key"] for o in r.options}
        self.assertTrue({"A", "A1", "A2", "AM"} <= keys)
        r2 = self.eng.handle_option(s, "A1")
        self.assertEqual(r2.kind, "question")
        self.assertEqual(s.node_id, "a1_root")

    def test_archived_variant_never_served(self):
        s = Session("t8")
        r = self.eng._price_reply(s, "C_CE_BA")        # archived 2024_06
        self.assertEqual(r.kind, "handoff")


if __name__ == "__main__":
    unittest.main()
