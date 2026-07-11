"""Disambiguation tree tests — pure logic, no client data. Safe anywhere."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fahrschule.disambiguation import NODES, TREES, Disambiguator


class TestTreeIntegrity(unittest.TestCase):
    def test_every_option_is_terminal_xor_goto(self):
        for node in NODES.values():
            for o in node.options:
                self.assertNotEqual(bool(o.variant), bool(o.goto),
                                    f"{node.id}/{o.key}: must have exactly one of variant/goto")

    def test_every_goto_points_to_existing_node(self):
        for node in NODES.values():
            for o in node.options:
                if o.goto:
                    self.assertIn(o.goto, NODES, f"{node.id}/{o.key} -> missing node {o.goto}")

    def test_every_tree_root_exists(self):
        for base, root in TREES.items():
            self.assertIn(root, NODES, f"{base} root {root} missing")


class TestWalks(unittest.TestCase):
    def setUp(self):
        self.d = Disambiguator()

    def test_b_automatic_resolves_to_b197(self):
        s = self.d.start("B")
        self.assertEqual(s.node.id, "b_situation")
        s = self.d.choose("b_situation", "neu")
        self.assertEqual(s.node.id, "b_combine")
        s = self.d.choose("b_combine", "nur_b")
        self.assertEqual(s.node.id, "b_transmission")
        s = self.d.choose("b_transmission", "automatik")
        self.assertEqual(s.kind, "resolved")
        self.assertEqual(s.variant_key, "B197")

    def test_b_manual_standard_resolves_to_b(self):
        self.d.choose("b_situation", "neu")
        self.d.choose("b_combine", "nur_b")
        self.d.choose("b_transmission", "manuell")
        s = self.d.choose("b_course", "standard")
        self.assertEqual(s.variant_key, "B")

    def test_b_manual_variants(self):
        self.assertEqual(self.d.choose("b_course", "intensiv").variant_key, "B_Intensiv")
        self.assertEqual(self.d.choose("b_course", "simulator").variant_key, "B_SIM")

    def test_b_combos_and_special_cases(self):
        self.assertEqual(self.d.choose("b_combine", "plus_be").variant_key, "B_BE")
        self.assertEqual(self.d.choose("b_combine", "plus_c1").variant_key, "B_C1")
        self.assertEqual(self.d.choose("b_situation", "wiedererteilung").variant_key, "B_Wiedererteilung")
        self.assertEqual(self.d.choose("b_situation", "wechsel").variant_key, "B_Wechsel")
        self.assertEqual(self.d.choose("b_situation", "automatik_aufheben").variant_key, "B78_Aufhebung")

    def test_b96_format(self):
        s = self.d.choose("b_combine", "nur_anhaenger_b96")
        self.assertEqual(s.node.id, "b96_format")
        self.assertEqual(self.d.choose("b96_format", "einzel").variant_key, "B96_Einzel")
        self.assertEqual(self.d.choose("b96_format", "gruppe").variant_key, "B96_Gruppe")

    def test_be_and_c_bgq(self):
        self.assertEqual(self.d.choose("be_root", "be_t").variant_key, "BE_T")
        s = self.d.choose("c_root", "c_ce_bgq")
        self.assertEqual(s.node.id, "c_bgq_time")
        s2 = self.d.choose("c_bgq_time", "tz")
        self.assertEqual(s2.variant_key, "C_CE_BGQ_TZ")
        self.assertEqual(s2.confidence, "needs_verification")

    def test_unknown_option_raises(self):
        with self.assertRaises(KeyError):
            self.d.choose("b_situation", "nonsense")

    def test_has_tree(self):
        self.assertTrue(self.d.has_tree("B"))
        self.assertFalse(self.d.has_tree("AM"))  # single variant, no tree needed

    def test_b_leaf_count(self):
        self.assertEqual(len(self.d.leaf_variants("B")), 18)


if __name__ == "__main__":
    unittest.main()
