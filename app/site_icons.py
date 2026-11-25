"""Safe, inline icon rendering for merchant content fields."""

from __future__ import annotations

import html
import re
from functools import lru_cache
from pathlib import Path

from fasthtml.common import Img, NotStr

from app.services import CommerceError

BUILTIN_ICON_PREFIX = "lucide:"
_ICON_DIRECTORY = Path(__file__).resolve().parents[1] / "static" / "vendor" / "lucide" / "icons"
_ICON_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_MEDIA_ICON = re.compile(r"/site-media/[A-Za-z0-9_-]{1,32}/[A-Za-z0-9_-]{1,32}")
_VIEWBOX = re.compile(r'\bviewBox="([^"]+)"')
_SVG_CONTENT = re.compile(r"\A\s*<svg\b[^>]*>(.*)</svg>\s*\Z", re.S)

# Keys already stored by the original icon widgets; they keep their built-in drawings.
LEGACY_ICON_REFERENCES = frozenset({
    "account", "arrow", "bag", "box", "calendar", "calories", "card", "cart", "cell",
    "chat", "check", "clock", "exercise", "facebook", "gift", "globe", "heart", "home", "instagram",
    "leaf", "lock", "mail", "map_pin", "menu", "molecule", "phone", "recycle", "return",
    "returns", "search", "shield", "shipping", "sparkle", "star", "sugar", "sun", "tag",
    "tested", "truck", "water",
})


@lru_cache(maxsize=1)
def builtin_icon_names() -> tuple[str, ...]:
    """Return the curated, vendored Lucide icon names in stable picker order."""
    if not _ICON_DIRECTORY.is_dir():
        return ()
    return tuple(sorted(
        path.stem for path in _ICON_DIRECTORY.glob("*.svg") if _ICON_NAME.fullmatch(path.stem)
    ))


def _builtin_name(value: str) -> str | None:
    if not isinstance(value, str) or not value.startswith(BUILTIN_ICON_PREFIX):
        return None
    name = value.removeprefix(BUILTIN_ICON_PREFIX)
    return name if name in builtin_icon_names() else None


def validate_icon_reference(value):
    """Accept only a curated icon, a site-media URL, or a backwards-compatible key."""
    if not isinstance(value, str):
        raise CommerceError("Choose an icon from the icon library.")
    if not value or _builtin_name(value) or _MEDIA_ICON.fullmatch(value) or value in LEGACY_ICON_REFERENCES:
        return value
    raise CommerceError("Choose an icon from the icon library.")


@lru_cache(maxsize=512)
def _builtin_source(name: str) -> tuple[str, str]:
    source = (_ICON_DIRECTORY / f"{name}.svg").read_text(encoding="utf-8")
    view_box = _VIEWBOX.search(source)
    content = _SVG_CONTENT.fullmatch(source)
    if not view_box or not content:
        raise ValueError(f"Invalid vendored icon: {name}")
    return view_box.group(1), content.group(1)


def _markup(view_box: str, content: str, *, css_class: str, size: int, stroke_width: str) -> str:
    class_attr = ' class="' + html.escape(css_class, quote=True) + '"' if css_class else ""
    return (
        '<svg' + class_attr + ' viewBox="'
        + html.escape(view_box, quote=True) + '" width="' + str(size) + '" height="'
        + str(size) + '" fill="none" stroke="currentColor" stroke-width="'
        + html.escape(stroke_width, quote=True)
        + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        + content + "</svg>"
    )


def builtin_icon_markup(value: str, *, css_class="", size=24, stroke_width="1.7") -> str | None:
    name = _builtin_name(value)
    if name is None:
        return None
    view_box, content = _builtin_source(name)
    return _markup(view_box, content, css_class=css_class, size=size, stroke_width=stroke_width)


def picker_icons() -> list[dict[str, str]]:
    """Return trusted inline previews for the editor's visual icon grid."""
    return [
        {
            "id": BUILTIN_ICON_PREFIX + name,
            "label": name.replace("-", " ").title(),
            "svg": builtin_icon_markup(BUILTIN_ICON_PREFIX + name, css_class="icon-picker-glyph"),
        }
        for name in builtin_icon_names()
    ]


def _custom_svg_markup(data: bytes, *, css_class: str, size: int, stroke_width: str) -> str | None:
    from app.site_media import prepare_svg_icon

    try:
        safe = prepare_svg_icon(data).decode("utf-8")
    except (CommerceError, UnicodeDecodeError):
        return None
    view_box = _VIEWBOX.search(safe)
    content = _SVG_CONTENT.fullmatch(safe)
    if not view_box or not content:
        return None
    return _markup(view_box.group(1), content.group(1), css_class=css_class, size=size,
                   stroke_width=stroke_width)


def custom_icon_markup(db, site, value: str, *, css_class="", size=24, stroke_width="1.7"):
    """Render a scoped custom SVG inline, or a scoped raster icon as an image."""
    if not isinstance(value, str) or not _MEDIA_ICON.fullmatch(value):
        return None
    from app.site_media import owned_media

    if value.split("/")[2] != site.id:
        return None
    try:
        media = owned_media(db, site, value.rsplit("/", 1)[-1])
    except CommerceError:
        return None  # Deleted media falls back to the caller's drawing.
    if media.content_type == "image/svg+xml" and media.data:
        markup = _custom_svg_markup(media.data, css_class=css_class, size=size,
                                    stroke_width=stroke_width)
        return NotStr(markup) if markup else None
    if media.content_type in {"image/jpeg", "image/png", "image/webp"}:
        return Img(src=f"/site-media/{site.id}/{media.id}", alt="", aria_hidden="true",
                   cls=css_class, width=size, height=size)
    return None


def render_icon(db, site, value, fallback, *, css_class="", size=24, stroke_width="1.7"):
    """Prefer a selected icon while retaining the caller's legacy rendering fallback."""
    markup = builtin_icon_markup(value, css_class=css_class, size=size, stroke_width=stroke_width)
    if markup is not None:
        return NotStr(markup)
    custom = custom_icon_markup(db, site, value, css_class=css_class, size=size,
                                stroke_width=stroke_width)
    return custom if custom is not None else fallback()
