"""A small, shared allow-list for merchant-authored rich text."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

ALLOWED_TAGS = frozenset({
    "p", "br", "h2", "h3", "h4", "strong", "b", "em", "i", "u", "a",
    "ul", "ol", "li", "blockquote", "hr",
})
VOID_TAGS = frozenset({"br", "hr"})
DROP_CONTENT_TAGS = frozenset({
    "script", "style", "iframe", "object", "embed", "svg", "math", "template",
})
BLOCK_TAGS = frozenset({"p", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "hr"})
_SAFE_REL = frozenset({"nofollow", "noopener", "noreferrer", "ugc", "sponsored"})
_ALLOWED_SCHEMES = frozenset({"http", "https", "mailto", "tel"})


def safe_rich_text_href(value: str | None) -> str:
    """Return an HTTP(S), mail, telephone or relative link, otherwise an empty string."""
    href = str(value or "").strip()
    if not href or any(ord(char) < 32 or ord(char) == 127 for char in href):
        return ""
    if "\\" in href:
        return ""

    scheme_probe = re.sub(r"[\x00-\x20\x7f]+", "", href)
    scheme_match = re.match(r"^([a-z][a-z0-9+.-]*):", scheme_probe, re.IGNORECASE)
    if scheme_match:
        scheme = scheme_match.group(1).lower()
        if scheme not in _ALLOWED_SCHEMES:
            return ""
        if scheme in {"http", "https"}:
            try:
                parsed = urlsplit(href)
            except ValueError:
                return ""
            if not parsed.netloc or not parsed.hostname or parsed.username:
                return ""
        if scheme in {"mailto", "tel"} and not href.split(":", 1)[1].strip():
            return ""
        return href

    if href.startswith("//"):
        return ""
    return href


class _RichTextSanitiser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.open_tags: list[str] = []
        self.dropped: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self.dropped:
            if tag in DROP_CONTENT_TAGS:
                self.dropped.append(tag)
            return
        if tag in DROP_CONTENT_TAGS:
            self.dropped.append(tag)
            return
        if tag not in ALLOWED_TAGS:
            return

        if tag in {"p", "h2", "h3", "h4"}:
            parent_block = next(
                (index for index in range(len(self.open_tags) - 1, -1, -1)
                 if self.open_tags[index] in {"p", "h2", "h3", "h4"}),
                None,
            )
            if parent_block is not None:
                while len(self.open_tags) > parent_block:
                    self._close_top()
        elif tag == "li" and self.open_tags and self.open_tags[-1] == "li":
            self._close_top()

        attributes = self._anchor_attributes(attrs) if tag == "a" else ""
        self.parts.append(f"<{tag}{attributes}>")
        if tag not in VOID_TAGS:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag in DROP_CONTENT_TAGS:
            return
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS and not self.dropped:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.dropped:
            if tag == self.dropped[-1]:
                self.dropped.pop()
            return
        if tag not in self.open_tags or tag in VOID_TAGS:
            return
        while self.open_tags:
            current = self.open_tags.pop()
            self.parts.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data):
        if not self.dropped:
            self.parts.append(html.escape(data, quote=False))

    def _anchor_attributes(self, attrs) -> str:
        values = dict(attrs)
        rendered = ""
        href = safe_rich_text_href(values.get("href"))
        if href:
            rendered += f' href="{html.escape(href, quote=True)}"'
        title = str(values.get("title") or "").strip()[:200]
        if title:
            rendered += f' title="{html.escape(title, quote=True)}"'
        target = str(values.get("target") or "").strip().lower()
        if target in {"_blank", "_self"}:
            rendered += f' target="{target}"'
        rel = {token.lower() for token in str(values.get("rel") or "").split()} & _SAFE_REL
        if target == "_blank":
            rel.update({"noopener", "noreferrer"})
        if rel:
            rendered += f' rel="{html.escape(" ".join(sorted(rel)), quote=True)}"'
        return rendered

    def _close_top(self):
        tag = self.open_tags.pop()
        self.parts.append(f"</{tag}>")

    def finish(self) -> str:
        while self.open_tags:
            self._close_top()
        return "".join(self.parts).strip()


def sanitise_rich_text(value: str | None) -> str:
    """Strip everything outside the shared merchant rich-text allow-list."""
    parser = _RichTextSanitiser()
    parser.feed(str(value or ""))
    parser.close()
    return parser.finish()
