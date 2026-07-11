"""Disambiguation decision trees — the follow-up-question logic.

When a class is ambiguous (e.g. "Klasse B" maps to 18 variants) the agent walks the
tree for that base class, asking one question at a time, until a single concrete
variant_key is reached. Each leaf is an exact variant that exists in the price store.

This module is pure routing logic derived from the client's offerings and
consultation documents — it contains no prices and no personal data, so it lives in
the public repository. Leaf → price mapping happens in the store; this only decides
*which* variant the user means.

Options whose meaning is not yet confirmed with the client (the "S" suffix, the
BGQ/TZ/VZ professional tokens) are marked confidence="needs_verification": the price
served is still exact, but the human-facing description of that option is provisional.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Option:
    key: str
    label_de: str
    label_en: str
    goto: str | None = None        # next node id
    variant: str | None = None     # terminal variant_key
    confidence: str = "official"


@dataclass(frozen=True)
class Node:
    id: str
    question_de: str
    question_en: str
    options: tuple[Option, ...]


def _n(node_id, qde, qen, options):
    return Node(node_id, qde, qen, tuple(options))


# --- node registry ----------------------------------------------------------
NODES: dict[str, Node] = {}


def _add(node: Node) -> None:
    NODES[node.id] = node


# ===== Class B =====
_add(_n("b_situation",
    "Geht es um einen Neuerwerb oder einen Sonderfall?",
    "Is this a new license or a special case?",
    [
        Option("neu", "Neuerwerb (erster Führerschein)", "New license (first)", goto="b_combine"),
        Option("umschreibung", "Ausländischen Führerschein umschreiben", "Convert a foreign license", goto="b_umschreibung"),
        Option("wiedererteilung", "Wiedererteilung nach Entzug", "Re-issue after withdrawal", variant="B_Wiedererteilung"),
        Option("wechsel", "Fahrschulwechsel", "Switching driving school", variant="B_Wechsel"),
        Option("automatik_aufheben", "Automatik-Beschränkung (78) aufheben", "Remove automatic-only (78) restriction", variant="B78_Aufhebung"),
    ]))
_add(_n("b_umschreibung",
    "Nur Klasse B umschreiben, oder auch C/CE?",
    "Convert only Class B, or also C/CE?",
    [
        Option("nur_b", "Nur Klasse B", "Only Class B", variant="B_Umschreiber"),
        Option("b_c_ce", "B und C/CE", "B and C/CE", variant="B_C_CE_Umschreiber"),
    ]))
_add(_n("b_combine",
    "Nur Pkw (Klasse B) oder kombiniert mit einer weiteren Klasse?",
    "Car only (Class B) or combined with another class?",
    [
        Option("nur_b", "Nur Pkw (B)", "Car only (B)", goto="b_transmission"),
        Option("plus_a", "B + Motorrad A", "B + motorcycle A", variant="B_A"),
        Option("plus_a2", "B + Motorrad A2", "B + motorcycle A2", variant="B_A2"),
        Option("plus_a1_196", "B + Leichtkraftrad A1 (196)", "B + light motorcycle A1 (196)", variant="B196"),
        Option("plus_be", "B + Anhänger (BE)", "B + trailer (BE)", variant="B_BE"),
        Option("plus_be_t", "B + Anhänger (BE) + Traktor (T)", "B + trailer (BE) + tractor (T)", variant="B_BE_T"),
        Option("plus_c1", "B + Lkw bis 7,5 t (C1)", "B + truck up to 7.5t (C1)", variant="B_C1"),
        Option("plus_t", "B + Traktor (T)", "B + tractor (T)", variant="B_T"),
        Option("nur_anhaenger_b96", "Ich habe B, nur Anhänger (B96)", "I have B, trailer only (B96)", goto="b96_format"),
    ]))
_add(_n("b96_format",
    "B96 als Einzel- oder Gruppenschulung?",
    "B96 as individual or group training?",
    [
        Option("einzel", "Einzelschulung", "Individual", variant="B96_Einzel"),
        Option("gruppe", "Gruppenschulung", "Group", variant="B96_Gruppe"),
    ]))
_add(_n("b_transmission",
    "Schaltwagen (manuell) oder Automatik?",
    "Manual or automatic transmission?",
    [
        Option("manuell", "Schaltwagen (manuell)", "Manual", goto="b_course"),
        Option("automatik", "Automatik (mit Schaltberechtigung, B197)", "Automatic (keeps manual entitlement, B197)", variant="B197"),
    ]))
_add(_n("b_course",
    "Standardkurs, Intensivkurs oder mit Simulator?",
    "Standard course, intensive course, or with simulator?",
    [
        Option("standard", "Standard", "Standard", variant="B"),
        Option("intensiv", "Intensivkurs", "Intensive", variant="B_Intensiv"),
        Option("simulator", "Mit Simulator", "With simulator", variant="B_SIM"),
    ]))

# ===== Class BE =====
_add(_n("be_root",
    "Nur Anhänger (BE) oder BE + Traktor (T)?",
    "Trailer only (BE) or BE + tractor (T)?",
    [
        Option("nur_be", "Nur BE", "BE only", variant="BE"),
        Option("be_t", "BE + Traktor (T)", "BE + tractor (T)", variant="BE_T"),
    ]))

# ===== Class A (motorcycle) =====
_add(_n("a_root",
    "Direkt Klasse A, A + Anhänger (BE), Vorbereitung auf A1, oder Sonderform AS?",
    "Direct Class A, A + trailer (BE), preparation for A1, or special form AS?",
    [
        Option("a", "Klasse A", "Class A", variant="A"),
        Option("a_be", "A + Anhänger (BE)", "A + trailer (BE)", variant="A_BE"),
        Option("a_vorb", "Vorbereitung A1", "Preparation for A1", variant="A_VorbA1"),
        Option("as", "Sonderform AS (Details klären)", "Special form AS (to be confirmed)", variant="AS", confidence="needs_verification"),
    ]))

# ===== Class A1 =====
_add(_n("a1_root",
    "Nur A1, oder A1 kombiniert mit B?",
    "A1 only, or A1 combined with B?",
    [
        Option("a1", "Nur A1", "A1 only", variant="A1"),
        Option("a1_b", "A1 + B", "A1 + B", variant="A1_B"),
    ]))

# ===== Class A2 =====
_add(_n("a2_root",
    "Klasse A2 oder Sonderform A2S (Details klären)?",
    "Class A2 or special form A2S (to be confirmed)?",
    [
        Option("a2", "Klasse A2", "Class A2", variant="A2"),
        Option("a2s", "Sonderform A2S", "Special form A2S", variant="A2S", confidence="needs_verification"),
    ]))

# ===== Class C (truck) =====
_add(_n("c_root",
    "Nur C, C + BE, C + CE, oder C/CE mit Grundqualifikation (BGQ)?",
    "C only, C + BE, C + CE, or C/CE with basic qualification (BGQ)?",
    [
        Option("nur_c", "Nur Klasse C", "Class C only", variant="C"),
        Option("c_be", "C + Anhänger (BE)", "C + trailer (BE)", variant="C_BE"),
        Option("c_ce", "C + CE", "C + CE", variant="C_CE"),
        Option("c_ce_bgq", "C/CE + Grundqualifikation (BGQ)", "C/CE + basic qualification (BGQ)", goto="c_bgq_time", confidence="needs_verification"),
    ]))
_add(_n("c_bgq_time",
    "BGQ in Teilzeit (TZ) oder Vollzeit (VZ)?",
    "BGQ part-time (TZ) or full-time (VZ)?",
    [
        Option("tz", "Teilzeit (TZ)", "Part-time (TZ)", variant="C_CE_BGQ_TZ", confidence="needs_verification"),
        Option("vz", "Vollzeit (VZ)", "Full-time (VZ)", variant="C_CE_BGQ_VZ", confidence="needs_verification"),
    ]))

# ===== Class C1 =====
_add(_n("c1_root",
    "Nur C1, oder C1 + Anhänger (C1E)?",
    "C1 only, or C1 + trailer (C1E)?",
    [
        Option("c1", "Nur C1", "C1 only", variant="C1"),
        Option("c1_c1e", "C1 + C1E", "C1 + C1E", variant="C1_C1E"),
    ]))

# base class -> root node id
TREES: dict[str, str] = {
    "B": "b_situation",
    "BE": "be_root",
    "A": "a_root",
    "A1": "a1_root",
    "A2": "a2_root",
    "C": "c_root",
    "C1": "c1_root",
}


@dataclass
class Step:
    kind: str                        # "question" | "resolved"
    node: Node | None = None
    variant_key: str | None = None
    confidence: str = "official"


class Disambiguator:
    """Walks a base class's tree. Stateless — the caller passes the current node id
    and the chosen option key each turn."""

    def has_tree(self, base_class: str) -> bool:
        return base_class in TREES

    def start(self, base_class: str) -> Step | None:
        root = TREES.get(base_class)
        return Step("question", node=NODES[root]) if root else None

    def choose(self, node_id: str, option_key: str) -> Step:
        node = NODES[node_id]
        opt = next((o for o in node.options if o.key == option_key), None)
        if opt is None:
            raise KeyError(f"unknown option '{option_key}' for node '{node_id}'")
        if opt.variant:
            return Step("resolved", variant_key=opt.variant, confidence=opt.confidence)
        return Step("question", node=NODES[opt.goto], confidence=opt.confidence)

    # --- introspection / validation ---
    def leaf_variants(self, base_class: str) -> set[str]:
        """All variant_keys reachable from a base class's tree."""
        seen: set[str] = set()
        stack = [TREES[base_class]]
        visited: set[str] = set()
        while stack:
            nid = stack.pop()
            if nid in visited:
                continue
            visited.add(nid)
            for o in NODES[nid].options:
                if o.variant:
                    seen.add(o.variant)
                elif o.goto:
                    stack.append(o.goto)
        return seen
