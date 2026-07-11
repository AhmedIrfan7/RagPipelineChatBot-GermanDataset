r"""Deterministic dialogue engine (guided-options, DE/EN, short-term memory).

Drives the accuracy-critical conversation with NO LLM in the price path:

    free text --resolve_class--> ambiguous --disambiguation tree--> one variant
                              \-> resolved -----------------------> one variant
                                                                        |
                                                       store.get_price (exact)
                                                                        |
                                              price card + official PDF link

The engine only ever presents prices returned verbatim by the store. When it cannot
resolve confidently, it hands off to the client's team instead of guessing. An LLM
layer can later wrap this for free-text understanding and nicer phrasing, but the
price path stays deterministic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .disambiguation import Disambiguator
from .store import Store
from .taxonomy import LICENSE_CLASSES

# --- lightweight language detection (guided mode; a real detector/LLM comes later)
_DE_HINTS = {"was", "kostet", "preis", "klasse", "führerschein", "fahrschule", "ich",
             "und", "möchte", "brauche", "auto", "anhänger", "ja", "nein", "oder",
             "wie", "viel", "lkw", "motorrad", "kosten"}
_EN_HINTS = {"what", "cost", "price", "class", "license", "licence", "driving", "want",
             "need", "car", "trailer", "yes", "no", "how", "much", "truck",
             "motorcycle", "the", "does"}


def detect_language(text: str) -> str:
    toks = {t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split()}
    de, en = len(toks & _DE_HINTS), len(toks & _EN_HINTS)
    return "en" if en > de else "de"


@dataclass
class Session:
    session_id: str
    language: str = "de"
    explicit_language: bool = False
    stage: str = "start"          # start | choose_base | disambiguating | done
    base_class: str | None = None
    node_id: str | None = None
    pending_bases: list[str] = field(default_factory=list)
    resolved_variant: str | None = None
    history: list[dict] = field(default_factory=list)


@dataclass
class Reply:
    kind: str                     # message | question | price | handoff
    text: str
    options: list[dict] = field(default_factory=list)   # [{key,label}]
    price: dict | None = None
    document: str | None = None


def _t(lang: str, de: str, en: str) -> str:
    return de if lang == "de" else en


class DialogueEngine:
    def __init__(self, store: Store, disambiguator: Disambiguator | None = None,
                 handoff_email: str | None = None, school_name: str | None = None):
        self.store = store
        self.d = disambiguator or Disambiguator()
        self.handoff_email = handoff_email or os.environ.get("HANDOFF_EMAIL", "")
        # client brand name is configured at runtime (env), never hardcoded in the repo
        self.school_name = school_name or os.environ.get("SCHOOL_NAME", "")

    # ---- entry points ------------------------------------------------------
    def greet(self, session: Session) -> Reply:
        de_school = f" bei {self.school_name}" if self.school_name else ""
        en_school = f" at {self.school_name}" if self.school_name else ""
        return Reply("message", _t(
            session.language,
            f"Willkommen{de_school}! Zu welcher Führerscheinklasse möchten Sie "
            "Informationen oder einen Preis? (z. B. B, BE, A, C …)",
            f"Welcome{en_school}! Which license class would you like info or a "
            "price for? (e.g. B, BE, A, C …)"))

    def handle_text(self, session: Session, text: str) -> Reply:
        if not session.explicit_language:
            session.language = detect_language(text)
        session.history.append({"role": "user", "text": text})
        r = self.store.resolve_class(text)
        if r.status == "resolved":
            return self._price_reply(session, r.variant_key)
        if r.status == "ambiguous":
            return self._begin_disambiguation(session, r.candidates)
        return self._handoff(session, unresolved=True)

    def handle_option(self, session: Session, option_key: str) -> Reply:
        if session.stage == "choose_base":
            return self._pick_base(session, option_key)
        if session.stage == "disambiguating" and session.node_id:
            try:
                step = self.d.choose(session.node_id, option_key)
            except KeyError:
                return self._help(session)
            if step.kind == "resolved":
                return self._price_reply(session, step.variant_key, step.confidence)
            session.node_id = step.node.id
            return self._question_reply(session, step.node, step.confidence)
        return self._help(session)

    # ---- internal steps ----------------------------------------------------
    def _begin_disambiguation(self, session: Session, candidates: list[str]) -> Reply:
        bases = sorted({self.store.by_key[c]["base_class"] for c in candidates})
        if len(bases) == 1:
            return self._start_base(session, bases[0])
        session.stage = "choose_base"
        session.pending_bases = bases
        opts = [{"key": b, "label": f"{b} — {LICENSE_CLASSES[b].name_de if session.language=='de' else LICENSE_CLASSES[b].name_en}"}
                if b in LICENSE_CLASSES else {"key": b, "label": b} for b in bases]
        return Reply("question", _t(session.language,
                                    "Welche Klasse meinen Sie?",
                                    "Which class do you mean?"), options=opts)

    def _pick_base(self, session: Session, base: str) -> Reply:
        if base not in self.store_bases():
            return self._help(session)
        return self._start_base(session, base)

    def _start_base(self, session: Session, base: str) -> Reply:
        session.base_class = base
        if self.d.has_tree(base):
            session.stage = "disambiguating"
            step = self.d.start(base)
            session.node_id = step.node.id
            return self._question_reply(session, step.node)
        variants = self.store.list_variants(base)
        if len(variants) == 1:
            return self._price_reply(session, variants[0]["variant_key"])
        return self._handoff(session)  # multi-variant base without a tree -> escalate

    def _question_reply(self, session, node, confidence="official") -> Reply:
        q = node.question_de if session.language == "de" else node.question_en
        opts = [{"key": o.key, "label": o.label_de if session.language == "de" else o.label_en}
                for o in node.options]
        return Reply("question", q, options=opts)

    def _price_reply(self, session: Session, variant_key: str, confidence="official") -> Reply:
        rec = self.store.get_price(variant_key)
        if rec is None:  # archived / not current / missing -> never serve a stale price
            return self._handoff(session, stale=True)
        session.stage = "done"
        session.resolved_variant = variant_key
        t = rec["totals"]
        title = rec.get("offer_title") or variant_key
        date = rec.get("offer_date") or (rec.get("date") or "")
        gesamt = t["gesamtbetrag"]
        ext = rec.get("external_fees_estimate_eur")
        doc = rec.get("source_file")

        if session.language == "de":
            text = (f"Für **{title}** (Klasse {variant_key}, Preisstand {date}) beträgt der "
                    f"**Gesamtbetrag {gesamt:.2f} €**")
            if t.get("netto") is not None:
                text += f" (netto {t['netto']:.2f} €"
                text += f" zzgl. {t['ust_percent']} % USt.)" if t.get("ust_percent") else ")"
            text += "."
            if ext:
                text += (f" Hinzu kommen externe Gebühren (TÜV, Bürgerbüro, Sehtest, "
                         f"Erste-Hilfe) von ca. {ext:.0f} €, die nicht von der Fahrschule "
                         f"berechnet werden.")
            text += " Das offizielle Preisblatt (PDF) stelle ich Ihnen als Download bereit."
            if confidence == "needs_verification":
                text += (" Hinweis: Details zu dieser Sonderform bestätigen wir noch — "
                         "der Preis selbst ist verbindlich aus dem Preisblatt.")
        else:
            text = (f"For **{title}** (class {variant_key}, price as of {date}), the "
                    f"**total is €{gesamt:.2f}**")
            if t.get("netto") is not None:
                text += f" (net €{t['netto']:.2f}"
                text += f" plus {t['ust_percent']}% VAT)." if t.get("ust_percent") else ")."
            else:
                text += "."
            if ext:
                text += (f" External fees (TÜV, city office, eye test, first-aid) of about "
                         f"€{ext:.0f} apply and are not charged by the driving school.")
            text += " I'll provide the official price sheet (PDF) as a download."
            if confidence == "needs_verification":
                text += (" Note: details of this special variant are still being confirmed — "
                         "the price itself is binding from the price sheet.")

        price = {
            "variant_key": variant_key,
            "title": title,
            "date": rec.get("date"),
            "gesamtbetrag": gesamt,
            "netto": t.get("netto"),
            "ust_percent": t.get("ust_percent"),
            "external_fees_estimate_eur": ext,
            "line_items": rec.get("line_items", []),
            "document": doc,
        }
        return Reply("price", text, price=price, document=doc)

    def _handoff(self, session: Session, unresolved=False, stale=False) -> Reply:
        if session.language == "de":
            base = ("Das lässt sich am besten persönlich klären." if not unresolved
                    else "Diese Anfrage konnte ich nicht eindeutig einer Klasse zuordnen.")
            if stale:
                base = "Für diese Variante liegt mir kein aktueller Preis vor."
            base += (f" Bitte kontaktieren Sie unser Team: {self.handoff_email}"
                     if self.handoff_email else " Bitte kontaktieren Sie unser Team.")
        else:
            base = ("This is best clarified with our team." if not unresolved
                    else "I couldn't match this to a specific class.")
            if stale:
                base = "I don't have a current price for that variant."
            base += (f" Please contact our team: {self.handoff_email}"
                     if self.handoff_email else " Please contact our team.")
        return Reply("handoff", base)

    def _help(self, session: Session) -> Reply:
        return Reply("message", _t(session.language,
                                   "Bitte nennen Sie eine Führerscheinklasse (z. B. B, BE, C).",
                                   "Please name a license class (e.g. B, BE, C)."))

    def store_bases(self) -> set[str]:
        return {r["base_class"] for r in self.store.by_key.values()}
