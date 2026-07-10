"""German driving-license class taxonomy.

This is *general* German licensing knowledge (StVG / FeV) plus the client's
filename conventions. It contains NO client-confidential data (no prices, no
business details), so it is safe to keep in the public repository.

Accuracy-first rule: entries the client's filenames use in a non-standard or
ambiguous way are marked ``confidence="needs_verification"``. The chatbot must
never *assert* a meaning we are not sure of — such codes trigger a clarifying
question or a human handoff instead of a guessed answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LicenseClass:
    code: str                    # canonical token, e.g. "B", "BE", "C1E"
    name_de: str
    name_en: str
    category: str                # "motorcycle" | "car" | "truck" | "trailer_ext" | "agricultural" | "moped"
    description_de: str
    description_en: str
    min_age: str = ""            # informational only
    confidence: str = "official" # "official" | "needs_verification"
    notes: str = ""


# ---------------------------------------------------------------------------
# Standard EU/German license classes (well-established, high confidence)
# ---------------------------------------------------------------------------
LICENSE_CLASSES: dict[str, LicenseClass] = {
    "Mofa": LicenseClass(
        "Mofa", "Mofa-Prüfbescheinigung", "Moped test certificate", "moped",
        "Einsitzige Kleinkrafträder bis 25 km/h.",
        "Single-seat mopeds up to 25 km/h.", min_age="15"),
    "AM": LicenseClass(
        "AM", "Klasse AM", "Class AM", "moped",
        "Zweirädrige Kleinkrafträder / Mopeds bis 45 km/h, leichte Quads.",
        "Two-wheeled mopeds up to 45 km/h and light quads.", min_age="15/16"),
    "A1": LicenseClass(
        "A1", "Klasse A1", "Class A1", "motorcycle",
        "Leichtkrafträder bis 125 cm³ und 11 kW.",
        "Light motorcycles up to 125 cc and 11 kW.", min_age="16"),
    "A2": LicenseClass(
        "A2", "Klasse A2", "Class A2", "motorcycle",
        "Krafträder bis 35 kW.",
        "Motorcycles up to 35 kW.", min_age="18"),
    "A": LicenseClass(
        "A", "Klasse A", "Class A", "motorcycle",
        "Krafträder ohne Leistungsbeschränkung.",
        "Motorcycles with no power restriction.", min_age="24 (or 20 with 2y A2)"),
    "B": LicenseClass(
        "B", "Klasse B", "Class B", "car",
        "Kraftfahrzeuge bis 3.500 kg, max. 8 Sitzplätze außer Fahrer.",
        "Vehicles up to 3,500 kg, max 8 passenger seats.", min_age="18"),
    "BE": LicenseClass(
        "BE", "Klasse BE", "Class BE", "trailer_ext",
        "Klasse B mit Anhänger über 750 kg (Zug bis 7.000 kg).",
        "Class B with a trailer over 750 kg (combination up to 7,000 kg).", min_age="18"),
    "C1": LicenseClass(
        "C1", "Klasse C1", "Class C1", "truck",
        "Lkw 3.500–7.500 kg.",
        "Trucks 3,500–7,500 kg.", min_age="18"),
    "C1E": LicenseClass(
        "C1E", "Klasse C1E", "Class C1E", "truck",
        "Klasse C1 mit Anhänger.",
        "Class C1 with trailer.", min_age="18"),
    "C": LicenseClass(
        "C", "Klasse C", "Class C", "truck",
        "Lkw über 3.500 kg.",
        "Trucks over 3,500 kg.", min_age="21 (18 with BKrFQG)"),
    "CE": LicenseClass(
        "CE", "Klasse CE", "Class CE", "truck",
        "Klasse C mit Anhänger (Sattelzug / Lastzug).",
        "Class C with trailer (articulated / road train).", min_age="21 (18 with BKrFQG)"),
    "L": LicenseClass(
        "L", "Klasse L", "Class L", "agricultural",
        "Land-/forstwirtschaftliche Zugmaschinen bis 40 km/h.",
        "Agricultural/forestry tractors up to 40 km/h.", min_age="16"),
    "T": LicenseClass(
        "T", "Klasse T", "Class T", "agricultural",
        "Land-/forstwirtschaftliche Zugmaschinen bis 60 km/h.",
        "Agricultural/forestry tractors up to 60 km/h.", min_age="16"),
}


# ---------------------------------------------------------------------------
# Suffixes / key numbers (Schlüsselzahlen) & course-type tokens used in the
# client's filenames. Each maps a filename token to its meaning + confidence.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Variant:
    token: str
    meaning_de: str
    meaning_en: str
    confidence: str = "official"    # "official" | "needs_verification"
    triggers_followup: bool = False # is this a disambiguation branch under a base class?


VARIANTS: dict[str, Variant] = {
    # --- established key numbers / extensions ---
    "B96": Variant("B96", "Schlüsselzahl B96: B + Anhänger, Zug 3.500–4.250 kg (Schulung, keine Prüfung).",
                   "Key number B96: B + trailer, combination 3,500–4,250 kg (training, no exam).",
                   triggers_followup=True),
    "B196": Variant("B196", "Schlüsselzahl 196: mit B auch Leichtkrafträder A1 (nur in DE).",
                    "Key number 196: A1 light motorcycles on a Class B license (Germany only).",
                    triggers_followup=True),
    "B197": Variant("B197", "Schlüsselzahl 197: Ausbildung auf Automatik, aber Schaltberechtigung bleibt.",
                    "Key number 197: trained on automatic but keeps manual-transmission entitlement.",
                    triggers_followup=True),
    "B78": Variant("B78", "Schlüsselzahl 78: Beschränkung auf Automatik.",
                   "Key number 78: automatic-transmission-only restriction.",
                   triggers_followup=True),
    "Aufhebung": Variant("Aufhebung", "Aufhebung einer Beschränkung (z. B. Automatik B78 entfernen).",
                         "Removal of a restriction (e.g. lifting the B78 automatic-only limit)."),
    "Einzel": Variant("Einzel", "Einzelbuchung / Einzelunterricht.",
                      "Individual booking / individual lesson."),
    "Gruppe": Variant("Gruppe", "Gruppenbuchung / Gruppenkurs.",
                      "Group booking / group course."),
    "Umschreiber": Variant("Umschreiber", "Umschreibung eines (ausländischen/alten) Führerscheins.",
                           "Conversion/transfer of a (foreign/old) driving license.",
                           triggers_followup=True),
    "Wiedererteilung": Variant("Wiedererteilung", "Wiedererteilung nach Entzug der Fahrerlaubnis.",
                               "Re-issuance of a license after withdrawal.",
                               triggers_followup=True),
    "Wechsel": Variant("Wechsel", "Wechsel (vermutlich Fahrschulwechsel).",
                       "Switch (likely a driving-school change).",
                       confidence="needs_verification"),
    "Intensiv": Variant("Intensiv", "Intensivkurs (verkürzte Ausbildungsdauer).",
                        "Intensive course (compressed schedule)."),
    "SIM": Variant("SIM", "Ausbildung mit Fahrsimulator.",
                   "Training that includes a driving simulator."),
    "VorbA1": Variant("VorbA1", "Vorbereitung/Vorstufe auf Klasse A1.",
                      "Preparation stage for Class A1.", confidence="needs_verification"),
    # --- professional truck driver qualification ---
    "BGQ": Variant("BGQ", "Beschleunigte Grundqualifikation (Berufskraftfahrer, BKrFQG).",
                   "Accelerated basic qualification for professional drivers (BKrFQG).",
                   confidence="needs_verification"),
    "TZ": Variant("TZ", "Teilzeit.", "Part-time.", confidence="needs_verification"),
    "VZ": Variant("VZ", "Vollzeit.", "Full-time.", confidence="needs_verification"),
    # --- client-specific tokens we are NOT sure about: must be verified, never guessed to the user ---
    "BA": Variant("BA", "Unklar — evtl. Bundesagentur für Arbeit (Förderung) oder Berufsausbildung.",
                  "Unclear — possibly job-agency funding (Bundesagentur für Arbeit) or vocational training.",
                  confidence="needs_verification"),
    "S": Variant("S", "Unklar — Suffix 'S' (z. B. AS/A2S). Bedeutung beim Kunden zu klären.",
                 "Unclear — 'S' suffix (e.g. AS/A2S). Meaning to be confirmed with the client.",
                 confidence="needs_verification"),
    "T_suffix": Variant("T_suffix", "Unklar — Suffix '_T' (z. B. B_T, BE_T). Bedeutung zu klären.",
                        "Unclear — '_T' suffix (e.g. B_T, BE_T). Meaning to be confirmed.",
                        confidence="needs_verification"),
}


# Tokens that, when appearing right after the date, indicate a NON-price-sheet
# document type rather than a license class.
DOC_TYPE_TOKENS = {
    "Preisaushang": "price_display",       # Preisaushang Drive-In Lkw
    "Beratungsprotokoll": "consultation_protocol",
    "Beratungsgespräch": "consultation_dialog",
    "Informationsbogen": "business_info",
}


# Real classes, longest first, so "A2S" peels "A2" (not "A") and "C1E" stays "C1E".
_CLASSES_BY_LEN = sorted(LICENSE_CLASSES, key=len, reverse=True)

# Glued key-number suffixes (Schlüsselzahlen) map to their B-variant token.
_KEYNUM_TO_VARIANT = {"96": "B96", "196": "B196", "197": "B197", "78": "B78"}


def _peel(first_token: str) -> tuple[str, str]:
    """Split a single glued token into (base_class, glued_suffix).

    "A2S" -> ("A2", "S"); "B96" -> ("B", "96"); "B197" -> ("B", "197");
    "CE" -> ("CE", ""); "C1E" -> ("C1E", ""); "A1" -> ("A1", "").
    """
    for cls in _CLASSES_BY_LEN:
        if first_token == cls:
            return cls, ""
        if first_token.startswith(cls):
            return cls, first_token[len(cls):]
    return first_token, ""


def base_class_of(token_string: str) -> str:
    """Return the canonical base license-class code from a variant token string.

    "B_BE_T" -> "B", "C_CE_BGQ_TZ" -> "C", "CE_BA" -> "CE", "A2S" -> "A2",
    "AS" -> "A", "B96_Einzel" -> "B", "B197" -> "B".
    """
    first = token_string.split("_")[0]
    base, _suffix = _peel(first)
    return base


def parse_price_token(variant_key: str) -> dict:
    """Decompose a price-sheet variant key into structured parts.

    Returns {base_class, sub_tokens} where sub_tokens is the normalized list of
    variant / key-number / suffix / funding tokens attached to the base class.
    """
    raw = [t for t in variant_key.split("_") if t]
    if not raw:
        return {"base_class": variant_key, "sub_tokens": []}
    base, glued = _peel(raw[0])
    sub: list[str] = []
    if glued:
        sub.append(_KEYNUM_TO_VARIANT.get(glued, glued))  # "96"->"B96", "S"->"S"
    sub.extend(raw[1:])
    return {"base_class": base, "sub_tokens": sub}


def token_confidence(sub_token: str) -> str:
    """official | needs_verification | unknown — for flagging uncertain tokens."""
    v = VARIANTS.get(sub_token)
    if v:
        return v.confidence
    return "unknown"
