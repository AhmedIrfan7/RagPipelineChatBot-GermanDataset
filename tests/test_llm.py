"""LLM adapter + LLM-assisted routing tests.

The suite never makes live API calls: LLMClient's no-key path is checked directly,
and dialogue integration uses a FakeLLM. (The real key is exercised by a separate
one-off smoke script, not by the unit suite.)"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fahrschule.dialogue import DialogueEngine, Session
from fahrschule.disambiguation import Disambiguator
from fahrschule.llm import LLMClient
from fahrschule.store import Store

PRICES_DIR = ROOT / "data" / "processed" / "prices"
MANIFEST = ROOT / "data" / "interim" / "manifest.json"
_have_data = PRICES_DIR.exists() and any(PRICES_DIR.glob("*.json"))


class FakeLLM:
    def __init__(self, intent=None, translation=None):
        self._intent, self._translation = intent, translation

    def extract_intent(self, text, class_codes):
        return self._intent

    def translate(self, text, target_language="English"):
        return self._translation


class TestLLMClientGraceful(unittest.TestCase):
    def test_no_key_is_unavailable_and_returns_none(self):
        c = LLMClient(api_key="")
        self.assertFalse(c.available())
        self.assertIsNone(c.extract_intent("hello", ["B"]))
        self.assertIsNone(c.translate("hallo"))


@unittest.skipUnless(_have_data, "real client data not present (local-only)")
class TestLLMRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store.from_dir(PRICES_DIR, MANIFEST)

    def test_llm_routes_free_text_to_class(self):
        llm = FakeLLM(intent={"intent": "price", "class": "B", "query": "klasse b"})
        eng = DialogueEngine(self.store, Disambiguator(), kb=None, llm=llm)
        s = Session("l1")
        # message with no class token and no signal -> deterministic misses -> LLM routes
        r = eng.handle_text(s, "ich würde gerne ein Fahrzeug mit vier Rädern fahren lernen")
        self.assertEqual(r.kind, "question")
        self.assertEqual(s.stage, "disambiguating")
        self.assertEqual(s.node_id, "b_situation")

    def test_llm_never_yields_price_directly(self):
        # even if the LLM claims price intent, the answer comes from the store via
        # disambiguation — never a number invented by the model
        llm = FakeLLM(intent={"intent": "price", "class": "Mofa", "query": "mofa"})
        eng = DialogueEngine(self.store, Disambiguator(), kb=None, llm=llm)
        s = Session("l2")
        r = eng.handle_text(s, "was brauche ich zum Rollerfahren mit 15")
        self.assertEqual(r.kind, "price")
        self.assertEqual(r.price["gesamtbetrag"], 236.96)   # Mofa, from the store

    def test_en_faq_is_translated(self):
        llm = FakeLLM(translation="Opening hours Kleve: Mon-Thu 08:30-18:00.")
        eng = DialogueEngine(self.store, Disambiguator(), kb=None, llm=llm)
        s = Session("l3", language="en", explicit_language=True)
        reply = eng._faq_reply(s, {"answer": "Öffnungszeiten Kleve: Mo-Do 08:30-18:00.",
                                   "source": "Standorte", "kind": "section"})
        self.assertEqual(reply.text, "Opening hours Kleve: Mon-Thu 08:30-18:00.")

    def test_no_llm_still_deterministic(self):
        eng = DialogueEngine(self.store, Disambiguator(), kb=None, llm=None)
        s = Session("l4")
        r = eng.handle_text(s, "total gibberish xyzzy")
        self.assertEqual(r.kind, "handoff")


if __name__ == "__main__":
    unittest.main()
