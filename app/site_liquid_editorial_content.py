"""Source-copy snapshot to canonical editable Liquid editorial documents."""

import json
from pathlib import Path

CONTENT = json.loads(Path(__file__).with_suffix(".json").read_text("utf-8"))


def flatten(value, path=""):
    """Use existing item fields; URLs retain the normal block URL validation."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield from flatten(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from flatten(child, f"{path}.{index}")
    else:
        field = "url" if path.rsplit(".", 1)[-1] in {"href", "ppbLink", "deeperHref"} else "image" if path.rsplit(".", 1)[-1] == "image" else "body"
        yield {"theme": path, field: str(value)}


def editorial_block(layout, content, **fields):
    return {"id": "editorial-" + layout, "type": "liquid_editorial", "version": 1,
            "layout": layout, "items": list(flatten(content)), **fields}


LEARN = {
    "label": "PLACEHOLDER – page coming soon",
    "heading": "How a tablet makes hydrogen water",
    "text": "Each tablet contains elemental magnesium. When it meets water, it reacts and releases molecular hydrogen, which dissolves into your drink: Mg + 2H₂O → Mg(OH)₂ + H₂.",
    "back": "Back to the tablets →", "href": "/products/hydrogen-tablets",
}
SCIENCE = {
    "label": "PLACEHOLDER – page coming soon", "heading": "The science",
    "benefits": [
        {"anchor": "cellular-health", "title": "Cellular health*", "text": "Supports cellular health.*"},
        {"anchor": "antioxidant", "title": "Antioxidant support*", "text": "Helps neutralize free radicals and supports the body's response to oxidative stress.*"},
        {"anchor": "exercise-recovery", "title": "Exercise & recovery", "text": "Researchers have studied hydrogen-rich water in the context of exercise, including muscle fatigue and recovery."},
        {"anchor": "digestive-balance", "title": "Digestive balance", "text": "Early research has looked at hydrogen and the body's natural balance in the digestive tract."},
    ],
    "back": "Back to the tablets →", "href": "/products/hydrogen-tablets",
    "disclaimer": CONTENT["disclaimer"],
}


def editorial_documents():
    w = CONTENT
    hero = {key: w[key] for key in ("label", "h1", "intro", "scroll", "chapters")}
    science_hero = {**w["science"], "scroll": w["scroll"]}
    basic = [editorial_block("hero", hero, image="", video="", poster="", alt=""), *[
        editorial_block(layout, w[key]) for layout, key in (
            ("basics-1", "ch1"), ("basics-2", "ch2"), ("basics-3", "ch3"),
            ("fresh", "ch6"), ("ways", "ch7"),
        )], editorial_block("closing", {**w["closing"], "page": "basics"}),
        editorial_block("disclaimer", {"text": w["disclaimer"]})]
    scientific = [editorial_block("science-hero", science_hero, image="", video="", poster="", alt=""),
                  editorial_block("myths", w["ch4"]), editorial_block("research", w["ch5"]),
                  editorial_block("closing", {**w["closing"], "page": "science"}),
                  editorial_block("disclaimer", {"text": w["disclaimer"]})]
    return [
        ("/why-hydrogen", "Hydrogen 101", "content", basic),
        ("/why-hydrogen/science", "What the science says", "content", scientific),
        ("/learn", LEARN["heading"], "content", [editorial_block("learn", LEARN)]),
        ("/science", "The science", "content", [editorial_block("science", SCIENCE)]),
    ]


EDITORIAL_DEFAULTS = {
    block["layout"]: {entry["theme"]: entry for entry in block["items"]}
    for _, _, _, blocks in editorial_documents() for block in blocks
}

EDITORIAL_FIELDS = {
    block["layout"]: {entry["theme"]: next(key for key in ("url", "image", "body") if key in entry)
                      for entry in block["items"]}
    for _, _, _, blocks in editorial_documents() for block in blocks
}


def editorial_data(block):
    root = {}
    for entry in block.get("items", []):
        parts = entry["theme"].split(".")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = entry.get("url", entry.get("image", entry.get("body", "")))

    def arrays(value):
        if not isinstance(value, dict):
            return value
        if value and all(k.isdigit() for k in value):
            return [arrays(value[k]) for k in sorted(value, key=int)]
        return {key: arrays(child) for key, child in value.items()}
    return arrays(root)


def validate_editorial(block):
    """Reject unknown, duplicate or incomplete structural source entries."""
    from app.services import CommerceError

    schema = EDITORIAL_FIELDS.get(block.get("layout"))
    if schema is None:
        raise CommerceError("Choose a supported Liquid editorial layout.")
    entries = block.get("items", [])
    seen = set()
    for entry in entries:
        theme = entry.get("theme")
        if not isinstance(theme, str) or theme not in schema or theme in seen:
            raise CommerceError("Choose a unique supported Liquid editorial entry.")
        if schema[theme] not in entry:
            raise CommerceError("Complete the Liquid editorial entry.")
        value = entry.get(schema[theme], "")
        if theme.rsplit(".", 1)[-1] in {"id", "icon", "kind"}:
            expected = EDITORIAL_DEFAULTS[block["layout"]][theme].get("body")
            if value != expected:
                raise CommerceError("Keep the supported Liquid editorial illustration.")
        numeric = theme.rsplit(".", 1)[-1] in {"people", "from", "to"} or theme.startswith("ticks.")
        if numeric:
            try:
                limit = 200 if theme.endswith(".people") else 5000
                if not 0 <= float(value) <= limit or not float(value).is_integer():
                    raise ValueError
            except (ValueError, TypeError):
                raise CommerceError("Use a valid Liquid editorial numeric value.") from None
        if theme == "page" and value not in {"basics", "science"}:
            raise CommerceError("Choose a supported Liquid editorial closing.")
        seen.add(theme)
    if seen != set(schema):
        raise CommerceError("Keep the complete Liquid editorial section structure.")
