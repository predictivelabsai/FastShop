"""US dietary-supplement wording checks for storefront content (brief §8).

Compliance is enforced where a merchant deliberately makes content public — page
publication and benefit-statement approval — not on draft saves or the seed fixture,
so editing stays frictionless while nothing non-compliant reaches shoppers. Banned
promotional wording and disease references block; hype words are advisory only.
"""

from __future__ import annotations

import re

from app.services import CommerceError

# Promotional wording the brief forbids everywhere (§8).
BANNED_TERMS = [
    "clinically proven", "proven", "guaranteed", "detox", "detoxify", "detoxifies",
    "anti-aging", "anti aging", "antiaging", "miracle", "cure", "cures", "cured", "curing",
]
# Named diseases/conditions must never be tied to a product (§8).
DISEASE_TERMS = [
    "diabetes", "diabetic", "cancer", "carcinoma", "tumor", "tumour", "parkinson",
    "alzheimer", "arthritis", "hypertension", "asthma", "depression", "dementia",
    "covid", "influenza", "stroke", "heart disease", "obesity",
]
# Empty-hype words the brief discourages (§1) — surfaced as advice, never blocking.
HYPE_TERMS = ["revolutionary", "passionate", "unlock your potential"]

# Section types that quote external studies; disease names in a citation are expected
# there, so only promotional wording is checked in them.
_RESEARCH_SECTIONS = {"research", "references"}


def _matches(text: str, terms: list[str]) -> list[str]:
    low = str(text or "").lower()
    found = []
    for term in terms:
        pattern = r"\b" + re.escape(term).replace(r"\ ", r"\s+") + r"\b"
        if re.search(pattern, low) and term not in found:
            found.append(term)
    return found


def scan_text(text: str, *, allow_disease: bool = True) -> dict[str, list[str]]:
    """Return banned/disease/hype terms found in a single string."""
    return {
        "banned": _matches(text, BANNED_TERMS),
        "diseases": _matches(text, DISEASE_TERMS) if allow_disease else [],
        "hype": _matches(text, HYPE_TERMS),
    }


def _section_strings(section: dict) -> list[str]:
    values = [section.get(key, "") for key in ("eyebrow", "heading", "body")]
    for item in section.get("items", []):
        if isinstance(item, dict):
            values.extend(str(item.get(key, "")) for key in ("heading", "body"))
    return [str(value) for value in values if value]


def scan_document(document: dict) -> dict[str, list[str]]:
    """Aggregate findings across a page document's editorial text."""
    banned: list[str] = []
    diseases: list[str] = []
    hype: list[str] = []
    for section in document.get("sections", []) if isinstance(document, dict) else []:
        if not isinstance(section, dict):
            continue
        check_disease = section.get("type") not in _RESEARCH_SECTIONS
        for value in _section_strings(section):
            found = scan_text(value, allow_disease=check_disease)
            banned += [t for t in found["banned"] if t not in banned]
            diseases += [t for t in found["diseases"] if t not in diseases]
            hype += [t for t in found["hype"] if t not in hype]
    return {"banned": banned, "diseases": diseases, "hype": hype}


def _blocking_message(findings: dict[str, list[str]], where: str) -> str | None:
    problems = []
    if findings["banned"]:
        problems.append("banned wording (" + ", ".join(findings["banned"]) + ")")
    if findings["diseases"]:
        problems.append("disease references (" + ", ".join(findings["diseases"]) + ")")
    if not problems:
        return None
    return (
        f"{where} cannot be published with " + " and ".join(problems)
        + ". Use research language (\"researchers have studied…\") and remove product health claims (brief §8)."
    )


def assert_document_compliant(document: dict) -> None:
    """Raise if a page carries banned wording or disease references."""
    message = _blocking_message(scan_document(document), "This page")
    if message:
        raise CommerceError(message)


def assert_claim_compliant(text: str) -> None:
    """Raise if an approved benefit statement is non-compliant or missing its asterisk."""
    message = _blocking_message(scan_text(text), "This benefit statement")
    if message:
        raise CommerceError(message)
    if "*" not in str(text):
        raise CommerceError(
            "An approved benefit statement must carry an asterisk (*) so it renders "
            "beside the FDA disclaimer (brief §8)."
        )
