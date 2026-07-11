"""Automated eval regression gate.

Loads the gitignored golden fixtures (confidential prices + the client's example
queries) and runs them end-to-end through the DETERMINISTIC engine (no LLM/embeddings,
so it is offline and CI-friendly):

  * price baseline — every current variant must still equal its signed-off Gesamtbetrag
  * FAQ (strict)   — questions BM25 always answers must answer, with the right fact
  * routing        — a class-price question must enter pricing disambiguation
  * adversarial    — unknown class / discount / nonsense must NEVER produce a price

Semantic-recall cases accept a handoff here; their positive behaviour is exercised by
the live scripts/06_eval.py (which uses the API key). Skipped without local fixtures.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fahrschule.dialogue import DialogueEngine, Session
from fahrschule.disambiguation import Disambiguator
from fahrschule.knowledge import KnowledgeBase
from fahrschule.store import Store

PRICES_DIR = ROOT / "data" / "processed" / "prices"
MANIFEST = ROOT / "data" / "interim" / "manifest.json"
KDIR = ROOT / "data" / "processed" / "knowledge"
GOLDEN = ROOT / "data" / "golden" / "expected_prices.json"
CASES = ROOT / "data" / "golden" / "eval_cases.json"

_have = (PRICES_DIR.exists() and any(PRICES_DIR.glob("*.json"))
         and GOLDEN.exists() and CASES.exists())


@unittest.skipUnless(_have, "golden fixtures / data not present (local-only)")
class TestEvalRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store.from_dir(PRICES_DIR, MANIFEST)
        kb = KnowledgeBase.from_dir(KDIR) if (KDIR / "rag_chunks.json").exists() else None
        # deterministic only: no llm, no semantic -> offline, no API calls
        cls.eng = DialogueEngine(cls.store, Disambiguator(), handoff_email="team@example.com", kb=kb)
        cls.expected = json.loads(GOLDEN.read_text(encoding="utf-8"))["prices"]
        cls.cases = json.loads(CASES.read_text(encoding="utf-8"))

    def test_price_baseline_no_drift(self):
        for variant, expected in self.expected.items():
            rec = self.store.get_price(variant)
            self.assertIsNotNone(rec, f"{variant} missing/not current")
            self.assertEqual(rec["totals"]["gesamtbetrag"], expected,
                             f"{variant}: price drifted from golden baseline")

    def _run(self, case):
        s = Session("eval")
        r = self.eng.handle_text(s, case["q"])
        self.assertIn(r.kind, case["kind"],
                      f"{case['q']!r}: kind {r.kind} not in {case['kind']}")
        for sub in case.get("contains", []):
            self.assertIn(sub, r.text, f"{case['q']!r}: missing {sub!r}")
        if case.get("base"):
            self.assertEqual(s.base_class, case["base"], f"{case['q']!r}: wrong base")
        return r

    def test_faq_strict(self):
        for c in self.cases["faq_strict"]:
            self._run(c)

    def test_pricing_routing(self):
        for c in self.cases["pricing"]:
            self._run(c)

    def test_adversarial_never_prices(self):
        for c in self.cases["adversarial"]:
            r = self._run(c)
            self.assertNotEqual(r.kind, "price",
                                f"{c['q']!r}: fabricated a price for an invalid request!")

    def test_faq_semantic_cases_are_safe(self):
        # without embeddings these may hand off; they must never mis-answer as a price
        for c in self.cases["faq_semantic"]:
            r = self._run(c)
            self.assertNotEqual(r.kind, "price")


if __name__ == "__main__":
    unittest.main()
