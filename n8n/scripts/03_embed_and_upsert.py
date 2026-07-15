"""n8n data prep, step 3: embed every prepared chunk and upsert into Qdrant.

Reuses fahrschule.llm.LLMClient.embed() (the same OpenAI text-embedding-3-small call
the Python chatbot uses) rather than reimplementing it. One Qdrant collection per
category; each point's payload carries the chunk text + metadata so retrieval nodes
in n8n can cite the source and (for pricing) the official PDF link.

Requires: Qdrant reachable at QDRANT_URL (default http://localhost:6333) and
OPENAI_API_KEY set (loaded from the repo-root .env).

Run:  .venv/Scripts/python.exe n8n/scripts/03_embed_and_upsert.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from fahrschule.llm import LLMClient  # noqa: E402

DATA_DIR = REPO_ROOT / "n8n" / "data"
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
EMBED_DIM = 1536  # text-embedding-3-small

CATEGORIES = ["pricing", "location_hours", "registration_docs", "courses_offerings", "general"]

# each category's chunks may come from more than one prepared file; "pricing" merges
# the 42 official price-sheet chunks (script 01) with the pricing-adjacent knowledge
# snippets (script 02, e.g. Hi-Five discount, funding, payment FAQs).
SOURCE_FILES = {
    "pricing": ["pricing_chunks.json", "pricing.json"],
}


def _http(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{QDRANT_URL}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode("utf-8"), "status": e.code}


def ensure_collection(name: str) -> None:
    _http("PUT", f"/collections/{name}", {
        "vectors": {"size": EMBED_DIM, "distance": "Cosine"},
    })


def point_id(category: str, source: str, text: str) -> int:
    h = hashlib.sha256(f"{category}|{source}|{text}".encode("utf-8")).hexdigest()
    return int(h[:15], 16)  # fits in a signed 64-bit int, stable across reruns (idempotent upsert)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / ".env")
    except Exception:
        pass

    llm = LLMClient()
    if not llm.available():
        sys.exit("ERROR: OPENAI_API_KEY not set (check .env at repo root).")

    ping = _http("GET", "/collections")
    if "error" in ping:
        sys.exit(f"ERROR: Qdrant not reachable at {QDRANT_URL}: {ping['error']}")

    total = 0
    for cat in CATEGORIES:
        filenames = SOURCE_FILES.get(cat, [f"{cat}.json"])
        items: list = []
        found_any = False
        for fname in filenames:
            path = DATA_DIR / fname
            if not path.exists():
                continue
            found_any = True
            items.extend(json.loads(path.read_text(encoding="utf-8")))
        if not found_any:
            print(f"  [skip] {cat}: none of {filenames} found (run scripts 01/02 first)")
            continue
        if not items:
            print(f"  [skip] {cat}: 0 chunks")
            continue

        ensure_collection(cat)
        vectors = llm.embed([it["text"] for it in items])
        if not vectors:
            sys.exit(f"ERROR: embedding call failed for category '{cat}'")

        points = []
        for it, vec in zip(items, vectors):
            points.append({
                "id": point_id(cat, it["source"], it["text"]),
                "vector": vec,
                "payload": {"source": it["source"], "text": it["text"], **it["metadata"]},
            })
        result = _http("PUT", f"/collections/{cat}/points?wait=true", {"points": points})
        if "error" in result:
            sys.exit(f"ERROR: upsert failed for '{cat}': {result['error']}")

        total += len(points)
        print(f"  {cat:20} {len(points):3} points upserted")

    print(f"\ntotal points upserted: {total}")

    print("\nverifying collection point counts via Qdrant API:")
    for cat in CATEGORIES:
        info = _http("GET", f"/collections/{cat}")
        count = info.get("result", {}).get("points_count", "?")
        print(f"  {cat:20} points_count={count}")


if __name__ == "__main__":
    main()
