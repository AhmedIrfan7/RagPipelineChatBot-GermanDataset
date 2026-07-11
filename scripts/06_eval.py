"""Step 12 / Phase 8 — live evaluation report.

Runs the golden price baseline + the client's example queries + adversarial probes
through the FULL production engine (semantic retrieval + LLM routing when an API key
is present) and prints a PASS/FAIL report. Use this for a human-readable accuracy
check; the offline gate lives in tests/test_eval_regression.py.

Run:  .venv/Scripts/python.exe scripts/06_eval.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fahrschule.dialogue import DialogueEngine, Session          # noqa: E402
from fahrschule.disambiguation import Disambiguator              # noqa: E402
from fahrschule.knowledge import KnowledgeBase                   # noqa: E402
from fahrschule.store import Store                               # noqa: E402

KDIR = ROOT / "data" / "processed" / "knowledge"
GOLDEN = ROOT / "data" / "golden" / "expected_prices.json"
CASES = ROOT / "data" / "golden" / "eval_cases.json"


def build_engine():
    store = Store.from_dir(ROOT / "data" / "processed" / "prices",
                           ROOT / "data" / "interim" / "manifest.json")
    kb = KnowledgeBase.from_dir(KDIR) if (KDIR / "rag_chunks.json").exists() else None
    llm = None
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass
    if os.environ.get("OPENAI_API_KEY") and kb is not None:
        from fahrschule.llm import LLMClient
        from fahrschule.embeddings import SemanticIndex
        client = LLMClient()
        if client.available():
            llm = client
            idx = SemanticIndex.build(kb.docs, client.embed, cache_path=KDIR / "embeddings.json")
            if idx:
                kb.attach_semantic(idx, lambda q: (client.embed([q]) or [None])[0])
    return DialogueEngine(store, Disambiguator(), handoff_email="info@example.com", kb=kb, llm=llm), store


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    eng, store = build_engine()
    mode = "FULL (semantic + LLM)" if eng.llm else "deterministic only (no API key)"
    print(f"=== Eval — {mode} ===\n")

    # 1) price baseline
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))["prices"]
    drift = [(k, store.get_price(k)["totals"]["gesamtbetrag"] if store.get_price(k) else None, v)
             for k, v in expected.items()
             if not store.get_price(k) or store.get_price(k)["totals"]["gesamtbetrag"] != v]
    print(f"PRICE BASELINE: {len(expected) - len(drift)}/{len(expected)} exact",
          "" if not drift else f"| DRIFT: {drift}")

    # 2) query cases
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    total = passed = 0
    for group, items in cases.items():
        print(f"\n[{group}]")
        for c in items:
            s = Session("eval")
            r = eng.handle_text(s, c["q"])
            ok = r.kind in c["kind"]
            ok = ok and all(sub in r.text for sub in c.get("contains", []))
            if c.get("base"):
                ok = ok and s.base_class == c["base"]
            total += 1
            passed += int(ok)
            print(f"  {'PASS' if ok else 'FAIL'}  [{r.kind:8}] {c['q'][:52]}")
    print(f"\n=== {passed}/{total} query cases pass | price baseline "
          f"{'OK' if not drift else 'DRIFT!'} ===")


if __name__ == "__main__":
    main()
