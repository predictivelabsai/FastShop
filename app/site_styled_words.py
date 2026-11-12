"""Safely apply merchant-configured styled words to Liquid storefront text."""

from __future__ import annotations

import copy
import html
import re
from html.parser import HTMLParser

from fastcore.xml import FT
from fasthtml.common import NotStr, Span, to_xml

from app.services import CommerceError

PRIMARY_COLOUR = "brand-primary"
H24YOU_STYLED_WORDS = [
    {"word": "One", "italic": True, "bold": False, "colour": PRIMARY_COLOUR},
    {"word": "Fizz", "italic": True, "bold": False, "colour": PRIMARY_COLOUR},
]
MAX_STYLED_WORDS = 30
MAX_WORD_LENGTH = 80
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_WORD = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)
_PROTECTED_TAGS = frozenset(
    {
        "script",
        "style",
        "title",
        "meta",
        "input",
        "textarea",
        "select",
        "option",
        "svg",
        "template",
    }
)
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


def validate_styled_words(value) -> list[dict]:
    """Return bounded, presentation-only word rules safe to store in site settings."""
    if not isinstance(value, list) or len(value) > MAX_STYLED_WORDS:
        raise CommerceError(f"Styled words must be a list of at most {MAX_STYLED_WORDS} entries.")
    result = []
    seen = set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"word", "italic", "bold", "colour"}:
            raise CommerceError("Each styled word needs word, italic, bold and colour.")
        word, colour = entry["word"], entry["colour"]
        if (
            not isinstance(word, str)
            or not 1 <= len(word) <= MAX_WORD_LENGTH
            or not _WORD.fullmatch(word)
        ):
            raise CommerceError(
                "Styled words must be one word using letters, numbers, hyphens or apostrophes."
            )
        if word in seen:
            raise CommerceError("Styled words must not repeat the same case-sensitive word.")
        if type(entry["italic"]) is not bool or type(entry["bold"]) is not bool:
            raise CommerceError("Styled word italic and bold values must be true or false.")
        if not isinstance(colour, str) or (colour != PRIMARY_COLOUR and not _HEX.fullmatch(colour)):
            raise CommerceError(
                "Styled word colour must be brand primary or a six-digit hex colour."
            )
        seen.add(word)
        result.append(
            {
                "word": word,
                "italic": entry["italic"],
                "bold": entry["bold"],
                "colour": colour.lower() if colour.startswith("#") else colour,
            }
        )
    return result


def styled_words_for(config: dict | None) -> list[dict]:
    """Read valid rules defensively so a malformed legacy JSON value cannot break a store."""
    try:
        settings = config if isinstance(config, dict) else {}
        return validate_styled_words(settings.get("styled_words", []))
    except CommerceError:
        return []


def _matcher(entries: list[dict]):
    by_word = {entry["word"]: entry for entry in entries}
    words = sorted(by_word, key=len, reverse=True)
    return re.compile(
        r"(?<!\w)(" + "|".join(re.escape(word) for word in words) + r")(?!\w)"
    ), by_word


def _word_span(word: str, entry: dict):
    attrs = {
        "data-styled-word": "true",
        "data-styled-word-italic": str(entry["italic"]).lower(),
        "data-styled-word-bold": str(entry["bold"]).lower(),
        "data-styled-word-colour": entry["colour"],
    }
    if entry["colour"] != PRIMARY_COLOUR:
        attrs["style"] = "--fs-styled-word-colour:" + entry["colour"]
    return Span(word, cls="fs-styled-word", **attrs)


def _style_text(value: str, pattern: re.Pattern[str], by_word: dict[str, dict]):
    parts = []
    position = 0
    for match in pattern.finditer(value):
        if match.start() > position:
            parts.append(value[position : match.start()])
        word = match.group(1)
        parts.append(_word_span(word, by_word[word]))
        position = match.end()
    if position == 0:
        return value
    if position < len(value):
        parts.append(value[position:])
    return tuple(parts)


def _classes(attrs) -> set[str]:
    if isinstance(attrs, dict):
        value = attrs.get("class", "")
    else:
        value = dict(attrs).get("class", "")
    return set(str(value or "").split())


def _styled_span(tag: str, attrs) -> bool:
    if tag != "span":
        return False
    values = attrs if isinstance(attrs, dict) else dict(attrs)
    return bool(values.get("class") or values.get("style") or "data-styled-word" in values)


class _RichTextStyler(HTMLParser):
    """Copy safe rich HTML while replacing text nodes only."""

    def __init__(self, pattern: re.Pattern[str], by_word: dict[str, dict]):
        super().__init__(convert_charrefs=True)
        self.pattern = pattern
        self.by_word = by_word
        self.output = []
        self.stack = []
        self.protected_depth = 0
        self.styled_depth = 0
        self.changed = False

    def _enter(self, tag: str, attrs) -> None:
        protected = tag in _PROTECTED_TAGS
        styled = bool(_classes(attrs) & {"fs-styled-word", "bottle-one"}) or _styled_span(tag, attrs)
        if tag in _VOID_TAGS:
            return
        self.stack.append((tag, protected, styled))
        self.protected_depth += int(protected)
        self.styled_depth += int(styled)

    def _leave(self, tag: str) -> None:
        if not self.stack or self.stack[-1][0] != tag:
            return
        _, protected, styled = self.stack.pop()
        self.protected_depth -= int(protected)
        self.styled_depth -= int(styled)

    def handle_starttag(self, tag, attrs) -> None:
        self.output.append(self.get_starttag_text())
        self._enter(tag.lower(), attrs)

    def handle_startendtag(self, tag, attrs) -> None:
        self.output.append(self.get_starttag_text())

    def handle_endtag(self, tag) -> None:
        tag = tag.lower()
        self.output.append("</" + tag + ">")
        self._leave(tag)

    def handle_data(self, data) -> None:
        if self.protected_depth or self.styled_depth:
            self.output.append(data)
            return
        parts = _style_text(data, self.pattern, self.by_word)
        if isinstance(parts, str):
            self.output.append(html.escape(parts, quote=False))
            return
        self.changed = True
        self.output.extend(
            html.escape(part, quote=False) if isinstance(part, str) else str(to_xml(part))
            for part in parts
        )

    def handle_comment(self, data) -> None:
        self.output.append("<!--" + data + "-->")

    def handle_decl(self, decl) -> None:
        self.output.append("<!" + decl + ">")

    def handle_pi(self, data) -> None:
        self.output.append("<?" + data + ">")


def _style_html(value: NotStr, pattern: re.Pattern[str], by_word: dict[str, dict]):
    parser = _RichTextStyler(pattern, by_word)
    parser.feed(str(value))
    parser.close()
    return NotStr("".join(parser.output)) if parser.changed else value


def _style_node(node, pattern: re.Pattern[str], by_word: dict[str, dict]):
    if isinstance(node, FT):
        tag = str(node.tag).lower()
        if (
            tag in _PROTECTED_TAGS
            or _classes(node.attrs) & {"fs-styled-word", "bottle-one"}
            or _styled_span(tag, node.attrs)
        ):
            return node
        node.children = tuple(_style_node(child, pattern, by_word) for child in node.children)
        return node
    if isinstance(node, (tuple, list)):
        return tuple(_style_node(child, pattern, by_word) for child in node)
    if isinstance(node, NotStr):
        return _style_html(node, pattern, by_word)
    if type(node) is str:
        return _style_text(node, pattern, by_word)
    return node


def apply_styled_words(node, entries: list[dict]):
    """Style matching visible text nodes without changing element attributes or metadata."""
    rules = validate_styled_words(copy.deepcopy(entries))
    if not rules:
        return node
    pattern, by_word = _matcher(rules)
    return _style_node(node, pattern, by_word)
