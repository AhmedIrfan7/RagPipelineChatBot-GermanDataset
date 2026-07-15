"""n8n workflow evaluation: runs the golden query set + adversarial probes + a
dedicated price-accuracy sweep against the live webhook, and reports HONEST results,
including where this RAG-based Pricing agent disagrees with the deterministic Python
system's golden prices. No rounding up.

Run:  .venv/Scripts/python.exe n8n/scripts/05_evaluate.py
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEBHOOK = "http://localhost:5678/webhook/fahrschule-native-chat/chat"
EVAL_CASES = REPO_ROOT / "data" / "golden" / "eval_cases.json"
EXPECTED_PRICES = REPO_ROOT / "data" / "golden" / "expected_prices.json"
OUT = REPO_ROOT / "n8n" / "eval_results_raw.json"

EUR_RE = re.compile(r"([\d.]+,\d{2})\s*(?:€|EUR)")


def de_num(s: str) -> float:
    return round(float(s.replace(".", "").replace(",", ".")), 2)


def ask(message: str) -> str:
    body = json.dumps({"chatInput": message, "sessionId": "eval"}).encode()
    req = urllib.request.Request(WEBHOOK, data=body, method="POST",
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8")).get("text", "")
    except Exception as e:
        return f"__ERROR__: {e}"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    results: dict = {"query_cases": [], "price_accuracy": []}

    # --- 1. client's example queries + adversarial probes -----------------
    cases = json.loads(EVAL_CASES.read_text(encoding="utf-8"))
    print("=" * 70)
    print("QUERY CASES (client examples + adversarial)")
    print("=" * 70)
    for group, items in cases.items():
        print(f"\n[{group}]")
        for c in items:
            reply = ask(c["q"])
            print(f"  Q: {c['q']}")
            print(f"  A: {reply[:200]}{'...' if len(reply) > 200 else ''}")
            results["query_cases"].append({"group": group, "q": c["q"], "reply": reply})
            print()

    # --- 2. direct price-accuracy sweep against the deterministic golden set
    expected = json.loads(EXPECTED_PRICES.read_text(encoding="utf-8"))["prices"]
    print("=" * 70)
    print("PRICE ACCURACY SWEEP (RAG pipeline vs. deterministic golden prices)")
    print("=" * 70)
    sample_keys = list(expected.items())  # test every current variant, no cherry-picking
    exact_matches = 0
    for variant_key, expected_price in sample_keys:
        q = f"Was kostet die Variante {variant_key}? Nenne nur den Gesamtbetrag."
        reply = ask(q)
        found_amounts = [de_num(m) for m in EUR_RE.findall(reply)]
        exact = expected_price in found_amounts
        exact_matches += int(exact)
        results["price_accuracy"].append({
            "variant_key": variant_key, "expected": expected_price,
            "found_amounts": found_amounts, "exact_match": exact, "reply": reply,
        })
        status = "MATCH" if exact else "MISMATCH"
        print(f"  [{status}] {variant_key:20} expected={expected_price:>10} "
              f"found={found_amounts}")

    total = len(sample_keys)
    print(f"\nPRICE ACCURACY: {exact_matches}/{total} exact matches "
          f"({100*exact_matches/total:.1f}%)")

    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nraw results written to {OUT.relative_to(REPO_ROOT)}")
    print(f"\nSUMMARY: {exact_matches}/{total} prices exact "
          f"({100*exact_matches/total:.1f}%) -- compare to the Python system's 44/44 (100%).")


if __name__ == "__main__":
    main()
