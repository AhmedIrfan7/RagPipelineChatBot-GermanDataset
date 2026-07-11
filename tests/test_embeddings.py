"""Semantic index + hybrid retrieval tests — deterministic fake vectors, no API calls."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fahrschule.embeddings import SemanticIndex, texts_hash
from fahrschule.knowledge import KnowledgeBase

DOCS = [
    {"kind": "faq", "source": "q1", "answer": "Antwort eins", "index_text": "q1 Antwort eins"},
    {"kind": "faq", "source": "q2", "answer": "Antwort zwei", "index_text": "q2 Antwort zwei"},
]


class TestSemanticIndex(unittest.TestCase):
    def test_search_nearest(self):
        idx = SemanticIndex([[1, 0, 0], [0, 1, 0]], DOCS)
        self.assertEqual(idx.search([0.9, 0.1, 0], 1)[0][1], 0)
        self.assertEqual(idx.search([0.1, 0.9, 0], 1)[0][1], 1)

    def test_cosine_is_normalized(self):
        idx = SemanticIndex([[2, 0, 0]], DOCS[:1])
        sim, _ = idx.search([5, 0, 0], 1)[0]
        self.assertAlmostEqual(sim, 1.0, places=6)

    def test_save_load_and_hash_invalidation(self):
        idx = SemanticIndex([[1, 0, 0], [0, 1, 0]], DOCS)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "emb.json"
            idx.save(p, "hash123")
            self.assertIsNotNone(SemanticIndex.load(p, "hash123"))
            self.assertIsNone(SemanticIndex.load(p, "different"))  # stale cache ignored

    def test_build_uses_cache(self):
        calls = {"n": 0}

        def fake_embed(texts):
            calls["n"] += 1
            return [[1, 0, 0], [0, 1, 0]]

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "emb.json"
            SemanticIndex.build(DOCS, fake_embed, cache_path=p)
            SemanticIndex.build(DOCS, fake_embed, cache_path=p)  # 2nd should hit cache
            self.assertEqual(calls["n"], 1)


class TestHybridSearch(unittest.TestCase):
    def _kb(self, embed_vec):
        kb = KnowledgeBase(DOCS, min_score=999)   # BM25 effectively disabled -> isolate semantic
        kb.attach_semantic(SemanticIndex([[1, 0, 0], [0, 1, 0]], DOCS),
                           embed_query=lambda q: embed_vec)
        return kb

    def test_semantic_hit(self):
        r = self._kb([1, 0, 0]).search("egal")
        self.assertIsNotNone(r)
        self.assertEqual(r["answer"], "Antwort eins")
        self.assertEqual(r["retriever"], "semantic")

    def test_below_threshold_falls_back_and_returns_none(self):
        # orthogonal query -> cosine 0 with both docs -> below threshold; BM25 disabled -> None
        self.assertIsNone(self._kb([0, 0, 1]).search("egal"))

    def test_no_semantic_uses_bm25(self):
        kb = KnowledgeBase(DOCS, min_score=0.0)   # semantic not attached
        r = kb.search("Antwort eins")
        self.assertIsNotNone(r)
        self.assertEqual(r["retriever"], "bm25")


if __name__ == "__main__":
    unittest.main()
