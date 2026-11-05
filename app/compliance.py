"""Return advisory copy findings for merchant-supplied storefront content."""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

# Promotional wording that may merit merchant review.
BANNED_TERMS = [
    "clinically proven", "proven", "guaranteed", "detox", "detoxify", "detoxifies",
    "anti-aging", "anti aging", "antiaging", "miracle", "cure", "cures", "cured", "curing",
]
# Named conditions that may merit merchant review.
DISEASE_TERMS = [
    "diabetes", "diabetic", "cancer", "carcinoma", "tumor", "tumour", "parkinson",
    "alzheimer", "arthritis", "hypertension", "asthma", "depression", "dementia",
    "covid", "influenza", "stroke", "heart disease", "obesity",
]
# Empty-hype wording surfaced as advice, never blocking.
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


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _readable(value):
    if isinstance(value, dict):
        return " ".join(_readable(text) for text in value.values())
    parser = _VisibleText()
    raw = str(value or "")
    parser.feed(unescape(raw))
    # Preserve the original plain-text scan as well as text joined across HTML
    # tags. Angle brackets in a plain heading must not hide prohibited wording.
    return raw + " " + "".join(parser.parts)


def _section_strings(section: dict) -> list[str]:
    values = [section.get(key, "") for key in ("eyebrow", "heading", "body")]
    for item in section.get("items", []):
        if isinstance(item, dict):
            values.extend(item.get(key, "") for key in ("heading", "body"))
    return [_readable(value) for value in values if value]


def _sections(blocks):
    for block in blocks:
        if isinstance(block, dict):
            yield block
            yield from _sections(block.get("children", []))


def scan_document(document: dict) -> dict[str, list[str]]:
    """Aggregate findings across a page document's editorial text."""
    banned: list[str] = []
    diseases: list[str] = []
    hype: list[str] = []
    if isinstance(document, dict) and "blog" in document:
        blog = document["blog"]
        values = [blog.get("author_name", ""), blog.get("author_bio", ""),
                  blog.get("category_slug", ""), *blog.get("tags", [])]
        category = document.get("category", "")
        values.extend(category.values() if isinstance(category, dict) else [category])
        for value in values:
            found = scan_text(value)
            banned += [t for t in found["banned"] if t not in banned]
            diseases += [t for t in found["diseases"] if t not in diseases]
            hype += [t for t in found["hype"] if t not in hype]
    blocks = document.get("blocks", document.get("sections", [])) if isinstance(document, dict) else []
    for section in _sections(blocks):
        if not isinstance(section, dict):
            continue
        check_disease = section.get("type") not in _RESEARCH_SECTIONS
        for value in _section_strings(section):
            found = scan_text(value, allow_disease=check_disease)
            banned += [t for t in found["banned"] if t not in banned]
            diseases += [t for t in found["diseases"] if t not in diseases]
            hype += [t for t in found["hype"] if t not in hype]
    return {"banned": banned, "diseases": diseases, "hype": hype}


def warning_message(findings: dict[str, list[str]]) -> str | None:
    """Format non-blocking copy findings for a merchant-facing notice."""
    flagged = list(dict.fromkeys(findings["banned"] + findings["diseases"] + findings["hype"]))
    if not flagged:
        return None
    return "Copy advisory: review flagged wording: " + ", ".join(flagged) + "."


def warning_for_values(values) -> str | None:
    """Scan nested shell copy values and return one concise advisory."""
    found = {"banned": [], "diseases": [], "hype": []}

    def visit(value):
        if isinstance(value, str):
            current = scan_text(value)
            for category in found:
                found[category].extend(term for term in current[category] if term not in found[category])
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(values)
    return warning_message(found)


def assert_document_compliant(document: dict) -> str | None:
    """Compatibility helper returning an advisory, never raising for copy."""
    return warning_message(scan_document(document))


def assert_copy_compliant(text: str) -> str | None:
    """Return a warning for merchant supplied shell copy, never block a save."""
    return warning_message(scan_text(text))


def assert_claim_compliant(text: str) -> str | None:
    """Return copy findings as an advisory, without asterisk or wording locks."""
    return warning_message(scan_text(text))
