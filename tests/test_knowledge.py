"""Knowledge retriever tests. Pure parsing/tokenization run anywhere; retrieval
quality (precision-first) is checked against real data when present."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fahrschule.knowledge import KnowledgeBase, _tokens, parse_faqs

KDIR = ROOT / "data" / "processed" / "knowledge"
_have_data = (KDIR / "rag_chunks.json").exists()


class TestParsing(unittest.TestCase):
    def test_parse_faqs(self):
        txt = ("1. Frage eins?\nAntwort eins.\n"
               "2. Frage zwei?\nAntwort zwei Zeile.\n"
               "3. Persönliche Voraussetzungen\nkein Fragezeichen hier")
        faqs = parse_faqs(txt)
        self.assertEqual(len(faqs), 2)          # item 3 has no '?', excluded
        self.assertEqual(faqs[0]["question"], "Frage eins?")
        self.assertEqual(faqs[0]["answer"], "Antwort eins.")

    def test_canon_and_stops(self):
        self.assertIn("anmeld", _tokens("Anmeldung"))
        self.assertIn("anmeld", _tokens("Ich melde mich an"))
        self.assertNotIn("klasse", _tokens("Klasse B"))    # class tokens stopped
        self.assertNotIn("b", _tokens("Klasse B"))          # single letter dropped


@unittest.skipUnless(_have_data, "real knowledge base not present (local-only)")
class TestRetrieval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = KnowledgeBase.from_dir(KDIR, min_score=4.0)

    def test_hours(self):
        r = self.kb.search("Wie sind die Öffnungszeiten am Standort Kleve?")
        self.assertIsNotNone(r)
        self.assertIn("Kleve", r["answer"])

    def test_document_validity(self):
        r = self.kb.search("Wie lange sind Sehtest und Erste-Hilfe-Kurs gültig?")
        self.assertIsNotNone(r)
        self.assertIn("2 Jahre", r["answer"])

    def test_required_documents(self):
        r = self.kb.search("Welche Unterlagen brauche ich zur Anmeldung?")
        self.assertIsNotNone(r)
        self.assertIn("Personalausweis", r["answer"])

    def test_english_hours(self):
        r = self.kb.search("What are the opening hours?", "en")
        self.assertIsNotNone(r)

    def test_nonsense_hands_off(self):
        self.assertIsNone(self.kb.search("asdifh qwerty nonsense"))

    def test_precision_no_wrong_vehicle_answer(self):
        # no reliable 'available vehicles' answer in corpus -> must NOT fabricate one
        r = self.kb.search("Welche Fahrzeuge stehen für Klasse B zur Verfügung?")
        self.assertIsNone(r)


if __name__ == "__main__":
    unittest.main()
