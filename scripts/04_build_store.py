"""Step 4 / Phase 3 — materialize the verified price records into a SQLite store.

Loads the arithmetic-verified JSON records, marks the canonical-current record per
class (older generations archived, never served by default), and writes a queryable
SQLite database. The DB is confidential (real prices) -> gitignored data/processed/.

Run:  .venv/Scripts/python.exe scripts/04_build_store.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from fahrschule.store import Store  # noqa: E402

PRICES_DIR = REPO_ROOT / "data" / "processed" / "prices"
MANIFEST = REPO_ROOT / "data" / "interim" / "manifest.json"
DB_PATH = REPO_ROOT / "data" / "processed" / "fahrschule.sqlite"

SCHEMA = """
DROP TABLE IF EXISTS line_items;
DROP TABLE IF EXISTS sheets;
CREATE TABLE sheets (
    variant_key   TEXT PRIMARY KEY,
    base_class    TEXT,
    date          TEXT,
    is_current    INTEGER,
    offer_title   TEXT,
    offer_date    TEXT,
    offer_nr      TEXT,
    gesamtbetrag  REAL,
    netto         REAL,
    ust_percent   INTEGER,
    ust_amount    REAL,
    external_fees REAL,
    source_file   TEXT,
    raw_sha256    TEXT
);
CREATE TABLE line_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    variant_key  TEXT REFERENCES sheets(variant_key),
    pos          TEXT,
    description  TEXT,
    anzahl       REAL,
    unit         TEXT,
    einzelpreis  REAL,
    ust_percent  INTEGER,
    gesamtpreis  REAL
);
CREATE INDEX idx_items_variant ON line_items(variant_key);
CREATE INDEX idx_sheets_base ON sheets(base_class, is_current);
"""


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    store = Store.from_dir(PRICES_DIR, MANIFEST)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)

    n_items = 0
    for key, r in store.by_key.items():
        t = r["totals"]
        con.execute(
            "INSERT INTO sheets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, r.get("base_class"), r.get("date"), int(r["is_current"]),
             r.get("offer_title"), r.get("offer_date"), r.get("offer_nr"),
             t.get("gesamtbetrag"), t.get("netto"), t.get("ust_percent"),
             t.get("ust_amount"), r.get("external_fees_estimate_eur"),
             r.get("source_file"), r.get("raw_text_sha256")),
        )
        for li in r["line_items"]:
            con.execute(
                "INSERT INTO line_items (variant_key,pos,description,anzahl,unit,"
                "einzelpreis,ust_percent,gesamtpreis) VALUES (?,?,?,?,?,?,?,?)",
                (key, li.get("pos"), li.get("description"), li.get("anzahl"),
                 li.get("unit"), li.get("einzelpreis"), li.get("ust_percent"),
                 li.get("gesamtpreis")),
            )
            n_items += 1
    con.commit()

    cur = con.execute("SELECT COUNT(*), SUM(is_current) FROM sheets").fetchone()
    archived = con.execute(
        "SELECT variant_key,date FROM sheets WHERE is_current=0 ORDER BY variant_key"
    ).fetchall()
    con.close()

    print(f"DB: {DB_PATH.relative_to(REPO_ROOT)}")
    print(f"sheets: {cur[0]} | current: {cur[1]} | archived: {cur[0]-cur[1]} | line_items: {n_items}")
    print(f"newest generation: {store.newest_generation}")
    print("archived (older generation, withheld from default lookups):")
    for k, d in archived:
        print(f"  - {k} ({d})")


if __name__ == "__main__":
    main()
