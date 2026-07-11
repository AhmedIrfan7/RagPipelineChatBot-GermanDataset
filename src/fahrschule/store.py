"""Deterministic price store + query layer — the layer the chatbot calls.

Design rule (the "never wrong" contract): prices are returned verbatim from the
verified records; this module never computes, estimates, or generates a price. It
resolves the user's intended class or, when the class is ambiguous, returns the
candidate set so the agent asks a follow-up instead of guessing.

Records are the arithmetic-verified JSON produced by scripts/03_extract_prices.py.
They are confidential and live in the gitignored data/processed/ tree; this module
loads them from disk locally, or accepts an injected list (used by tests with
synthetic data so logic can be tested without shipping real prices).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .taxonomy import LICENSE_CLASSES, base_class_of

# Free-text synonyms -> base license class(es). Group synonyms map to several
# bases and therefore always force a disambiguation question.
SYNONYMS: dict[str, list[str]] = {
    "auto": ["B"], "pkw": ["B"], "car": ["B"], "wagen": ["B"],
    "automatik": ["B"], "automatic": ["B"],
    "motorrad": ["A", "A1", "A2", "AM"], "motorcycle": ["A", "A1", "A2", "AM"],
    "bike": ["A", "A1", "A2", "AM"], "roller": ["AM", "A1"], "moped": ["AM", "Mofa"],
    "lkw": ["C", "C1", "CE", "C1E"], "truck": ["C", "C1", "CE", "C1E"],
    "laster": ["C", "CE"],
    "anhänger": ["BE", "B"], "anhaenger": ["BE", "B"], "trailer": ["BE", "B"],
    "traktor": ["L", "T"], "tractor": ["L", "T"], "trecker": ["L", "T"],
    "bus": ["C"],  # (no D-class sheets in dataset -> will resolve to not_found/handoff)
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

# case-insensitive lookup of class codes (LICENSE_CLASSES has mixed case, e.g. "Mofa")
_CLASS_BY_UPPER = {code.upper(): code for code in LICENSE_CLASSES}

# Key-number tokens users actually type -> the specific variant key(s) they mean.
# B96 stays ambiguous (Einzel vs Gruppe) so the agent asks which format.
_KEYNUM_RULES: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"^b?197$"), ["B197"]),
    (re.compile(r"^b?196$"), ["B196"]),
    (re.compile(r"^b?78$"), ["B78_Aufhebung"]),
    (re.compile(r"^b?96$"), ["B96_Einzel", "B96_Gruppe"]),
]


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip()


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(_norm(text).lower())


@dataclass
class ResolveResult:
    status: str                      # "resolved" | "ambiguous" | "not_found"
    variant_key: str | None = None
    base_class: str | None = None
    candidates: list[str] | None = None
    matched_on: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "variant_key": self.variant_key,
            "base_class": self.base_class,
            "candidates": self.candidates,
            "matched_on": self.matched_on,
        }


class Store:
    def __init__(self, records: list[dict], newest_generation: str | None = None):
        # newest generation = max date if not provided
        dates = [r.get("date") for r in records if r.get("date")]
        self.newest_generation = newest_generation or (max(dates) if dates else None)
        self.by_key: dict[str, dict] = {}
        for r in records:
            key = r["variant_key"]
            r = dict(r)
            r["is_current"] = (r.get("date") == self.newest_generation)
            self.by_key[key] = r
        # current variant keys, longest first (so "B197" wins over "B")
        self._current_keys = sorted(
            (k for k, r in self.by_key.items() if r["is_current"]),
            key=len, reverse=True,
        )

    # ---- loading -----------------------------------------------------------
    @classmethod
    def from_dir(cls, prices_dir: str | Path, manifest_path: str | Path | None = None) -> "Store":
        prices_dir = Path(prices_dir)
        records = [json.loads(p.read_text(encoding="utf-8"))
                   for p in sorted(prices_dir.glob("*.json"))]
        newest = None
        if manifest_path and Path(manifest_path).exists():
            newest = json.loads(Path(manifest_path).read_text(encoding="utf-8")).get("newest_generation")
        return cls(records, newest_generation=newest)

    # ---- deterministic lookups --------------------------------------------
    def get_price(self, variant_key: str, allow_archived: bool = False) -> dict | None:
        """Exact price record for a variant, or None. Archived (older-generation)
        records are withheld unless explicitly allowed — never serve a stale price."""
        rec = self.by_key.get(variant_key)
        if rec is None:
            return None
        if not rec["is_current"] and not allow_archived:
            return None
        return rec

    def list_variants(self, base_class: str, current_only: bool = True) -> list[dict]:
        out = []
        for key, rec in self.by_key.items():
            if rec.get("base_class") != base_class:
                continue
            if current_only and not rec["is_current"]:
                continue
            out.append({
                "variant_key": key,
                "offer_title": rec.get("offer_title"),
                "gesamtbetrag": rec["totals"]["gesamtbetrag"],
            })
        return sorted(out, key=lambda x: x["variant_key"])

    def get_document_link(self, variant_key: str) -> str | None:
        rec = self.by_key.get(variant_key)
        return rec["source_file"] if rec else None

    # ---- class resolution (routing / disambiguation gate) -----------------
    def resolve_class(self, text: str) -> ResolveResult:
        """Map free text to a specific current variant, or return the candidate
        set when the class is ambiguous so the agent asks a follow-up. A bare base
        class (e.g. "Klasse B") with several variants is intentionally ambiguous —
        the bot must ask, not assume the plain variant."""
        toks = _tokens(text)
        candidates: list[str] = []
        used_keynum = False

        # 1) explicit key-number tokens (B197, 196, B96 ...)
        for t in toks:
            for rx, keys in _KEYNUM_RULES:
                if rx.match(t):
                    used_keynum = True
                    candidates += [k for k in keys
                                   if k in self.by_key and self.by_key[k]["is_current"]]

        # 2) base-class tokens + synonyms -> all current variants of that base
        bases: list[str] = []
        for t in toks:
            up = t.upper()
            if up in _CLASS_BY_UPPER:
                bases.append(_CLASS_BY_UPPER[up])   # canonical case (e.g. "MOFA" -> "Mofa")
            elif t in SYNONYMS:
                bases.extend(SYNONYMS[t])
        bases = list(dict.fromkeys(bases))
        for b in bases:
            candidates += [v["variant_key"] for v in self.list_variants(b)]

        candidates = sorted(set(candidates))
        if len(candidates) == 1:
            return ResolveResult("resolved", variant_key=candidates[0],
                                 base_class=self.by_key[candidates[0]].get("base_class"),
                                 matched_on="single-candidate")
        if len(candidates) > 1:
            base = bases[0] if (len(bases) == 1 and not used_keynum) else None
            return ResolveResult("ambiguous", base_class=base,
                                 candidates=candidates, matched_on="multi-candidate")
        return ResolveResult("not_found", matched_on="no-match")
