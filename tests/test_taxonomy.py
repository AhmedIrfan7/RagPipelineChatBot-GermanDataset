"""Pure-logic tests for the taxonomy parser. No client data required — safe to run
anywhere (CI included)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fahrschule.taxonomy import base_class_of, parse_price_token, token_confidence


class TestBaseClassOf(unittest.TestCase):
    def test_glued_suffixes(self):
        self.assertEqual(base_class_of("A2S"), "A2")
        self.assertEqual(base_class_of("AS"), "A")

    def test_keynumbers_group_under_base(self):
        self.assertEqual(base_class_of("B197"), "B")
        self.assertEqual(base_class_of("B96_Einzel"), "B")
        self.assertEqual(base_class_of("B78_Aufhebung"), "B")

    def test_multichar_classes(self):
        self.assertEqual(base_class_of("CE_BA"), "CE")
        self.assertEqual(base_class_of("C1E"), "C1E")
        self.assertEqual(base_class_of("C_CE_BGQ_TZ"), "C")
        self.assertEqual(base_class_of("A1_B"), "A1")


class TestParsePriceToken(unittest.TestCase):
    def test_keynumber_and_format(self):
        p = parse_price_token("B96_Einzel")
        self.assertEqual(p["base_class"], "B")
        self.assertIn("B96", p["sub_tokens"])
        self.assertIn("Einzel", p["sub_tokens"])

    def test_professional_truck(self):
        p = parse_price_token("C_CE_BGQ_TZ")
        self.assertEqual(p["base_class"], "C")
        self.assertEqual(p["sub_tokens"], ["CE", "BGQ", "TZ"])

    def test_glued_suffix_token(self):
        p = parse_price_token("A2S")
        self.assertEqual(p["base_class"], "A2")
        self.assertEqual(p["sub_tokens"], ["S"])


class TestTokenConfidence(unittest.TestCase):
    def test_confidence_levels(self):
        self.assertEqual(token_confidence("BGQ"), "needs_verification")
        self.assertEqual(token_confidence("Wechsel"), "needs_verification")
        self.assertEqual(token_confidence("Einzel"), "official")
        self.assertEqual(token_confidence("CE"), "unknown")  # class token, not a variant


if __name__ == "__main__":
    unittest.main()
