"""Fixed Liquid SVG glyphs avoid operating-system symbol-font substitutions."""
import re

from fastcore.xml import FT
from fasthtml.common import NotStr, Span

PATHS = {
    "↗": '<path d="M5 19 19 5M5 5h14v14"/>',
    "↻": '<path d="M20 11a8 8 0 1 0-2 7M20 4v7h-7"/>',
    "↓": '<path d="M12 5v14m-7-7 7 7 7-7"/>',
    "↑": '<path d="M12 19V5m-7 7 7-7 7 7"/>',
    "←": '<path d="M19 12H5m7-7-7 7 7 7"/>',
    "→": '<path d="M5 12h14m-7-7 7 7-7 7"/>',
    "✓": '<path d="m5 12 4 4L19 6"/>',
    "★": '<path fill="currentColor" d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z"/>',
    "☆": '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z"/>',
    "Ⅱ": '<path d="M8 5v14M16 5v14"/>',
    "▷": '<path d="m7 4 14 8-14 8Z"/>',
}


def glyphs(node):
    if isinstance(node, FT):
        if node.tag in {"a", "button"} and not node.attrs.get("aria-label"):
            text = "".join(child for child in node.children if type(child) is str)
            if text.strip() and any(char in text for char in PATHS):
                node.attrs["aria-label"] = text
        if node.tag not in {"script", "style", "title", "textarea", "option"}:
            node.children = tuple(glyphs(child) for child in node.children)
    elif isinstance(node, (tuple, list)):
        return tuple(glyphs(child) for child in node)
    elif type(node) is str and any(char in node for char in PATHS):
        return Span(*[
            NotStr('<svg class="liquid-text-icon" viewBox="0 0 24 24" width="16" height="16" '
                   'fill="none" stroke="currentColor" stroke-width="1.5" '
                   'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                   + PATHS[part] + '</svg>') if part in PATHS else part
            for part in re.split("([" + "".join(PATHS) + "])", node) if part
        ], cls="liquid-glyph-text")
    return node
