"""Layout and media pack: plain registry data and theme-neutral renderers."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from fasthtml.common import (
    A,
    Button,
    Div,
    Iframe,
    Img,
    Input,
    Label,
    Li,
    NotStr,
    P,
    Section,
    Span,
    Ul,
    ft_hx,
)

from app.services import CommerceError

# Application-owned line drawings. Merchant input selects a name, never SVG markup.
ICON_PATHS = {
    "truck": '<path d="M3 6h11v11H3zM14 10h4l3 4v3h-7M6 17a2 2 0 1 0 0 4 2 2 0 0 0 0-4m12 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4"/>',
    "return": '<path d="M8 4 3 9l5 5M3 9h11a6 6 0 0 1 0 12h-3"/>',
    "lock": '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
    "leaf": '<path d="M20 3C4 2 2 12 6 17s15 3 14-14ZM5 21 16 9"/>',
    "star": '<path d="m12 3 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1Z"/>',
    "heart": '<path d="M20 5a5 5 0 0 0-8 1 5 5 0 0 0-8-1c-6 6 8 16 8 16S26 11 20 5Z"/>',
    "chat": '<path d="M21 11a9 8 0 0 1-9 8H7l-4 3V11a9 8 0 0 1 18 0ZM7 9h10M7 13h6"/>',
    "gift": '<path d="M3 9h18v4H3zM5 13v8h14v-8M12 9v12M12 9C2 9 5 0 9 4l3 5Zm0 0c10 0 7-9 3-5l-3 5Z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
    "tag": '<path d="M3 3h8l10 10-8 8L3 11ZM7 7h.01"/>',
    "shield": '<path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6ZM8 12l3 3 5-6"/>',
    "check": '<path d="m4 12 5 5L20 6"/>',
    "phone": '<path d="m4 3 5 1 1 5-3 2c2 3 3 4 6 6l2-3 5 1 1 5c-8 5-22-9-17-17Z"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7"/>',
    "map_pin": '<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
    "card": '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M2 9h20M6 15h4"/>',
    "recycle": '<path d="m8 5 4-3 4 7M12 2 8 9M20 10l2 5-4 7M22 15h-8M13 21H5l-3-5M5 21l4-7"/>',
    "sparkle": '<path d="m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3ZM20 2v4M18 4h4"/>',
    "box": '<path d="m12 2 10 5v10l-10 5L2 17V7ZM2 7l10 5 10-5M12 12v10M7 4l10 5"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><ellipse cx="12" cy="12" rx="4" ry="10"/><path d="M2 12h20"/>',
    "bag": '<path d="M5 8h14l2 13H3ZM8 8V6a4 4 0 0 1 8 0v2"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 1v3M12 20v3M1 12h3M20 12h3M4 4l2 2M18 18l2 2M4 20l2-2M18 6l2-2"/>',
    "home": '<path d="m2 11 10-9 10 9M5 9v13h14V9M9 22v-8h6v8"/>',
    "search": '<circle cx="10" cy="10" r="7"/><path d="m15 15 7 7"/>',
}


def video_embed_url(value):
    """Accept only recognised HTTPS video URLs and discard tracking parameters."""
    if not value:
        return ""
    if not isinstance(value, str):
        raise CommerceError("Choose a YouTube or Vimeo HTTPS URL.")
    try:
        url = urlsplit(value)
        if url.scheme != "https" or url.username or url.password or url.port not in {None, 443}:
            raise ValueError
        host, path = (url.hostname or "").lower(), url.path.strip("/")
        identifier = ""
        if host in {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "youtube-nocookie.com",
            "www.youtube-nocookie.com",
        }:
            identifier = (
                parse_qs(url.query).get("v", [""])[0]
                if path == "watch"
                else path.split("/")[-1]
                if path.startswith(("embed/", "shorts/"))
                else ""
            )
        elif host == "youtu.be":
            identifier = path
        if identifier and re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
            return "https://www.youtube-nocookie.com/embed/" + identifier
        if host in {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}:
            identifier = path.removeprefix("video/")
            if re.fullmatch(r"[0-9]{1,15}", identifier):
                return "https://player.vimeo.com/video/" + identifier + "?dnt=1"
    except ValueError:
        pass
    raise CommerceError("Choose a YouTube or Vimeo HTTPS URL.")


def validate_video_url(value):
    video_embed_url(value)
    return value


def pack_definitions(definition):
    """Build definitions without importing the owning registry (no circular import)."""

    def text(kind="text", **extra):
        maximum = 4000 if kind == "textarea" else 2048 if kind in {"image", "url"} else 240
        return {"kind": kind, "max_length": maximum, **extra}

    def choice(*values):
        return {"kind": "select", "options": values}

    def number(minimum, maximum, unit=""):
        return {"kind": "range", "min": minimum, "max": maximum, "unit": unit}

    def items(defaults, fields, minimum, maximum):
        return {"defaults": defaults, "fields": fields, "min": minimum, "max": maximum}

    align = choice("left", "centre", "right")
    positions = choice(
        "top-left",
        "top-centre",
        "top-right",
        "centre-left",
        "centre",
        "centre-right",
        "bottom-left",
        "bottom-centre",
        "bottom-right",
    )
    height = choice("small", "medium", "large", "adapt")
    slide = {
        "image": "",
        "alt": "Describe your image",
        "heading": "Your heading",
        "body": "Add your content.",
        "button": "Explore",
        "link": "/",
    }
    slide_fields = {
        "image": text("image"),
        "alt": text(),
        "heading": text(),
        "body": text("textarea"),
        "button": text(),
        "link": text("url"),
    }
    logo = {"image": "", "alt": "Your logo", "link": ""}
    icon = {"icon": "box", "heading": "Your feature", "body": "Add a useful detail.", "link": ""}
    spot = {"x": 50, "y": 50, "heading": "Your detail", "body": "Add your content.", "link": ""}
    return {
        "slideshow": definition(
            "Slideshow",
            "Media",
            {
                "items": [slide.copy()],
                "autoplay": False,
                "interval": 5,
                "show_arrows": True,
                "show_dots": True,
                "height": "medium",
                "text_position": "centre",
                "overlay_opacity": 30,
            },
            {
                "autoplay": {"kind": "toggle"},
                "interval": number(3, 15, "s"),
                "show_arrows": {"kind": "toggle"},
                "show_dots": {"kind": "toggle"},
                "height": height,
                "text_position": positions,
                "overlay_opacity": number(0, 80, "%"),
            },
            items(slide, slide_fields, 1, 8),
        ),
        "image_banner": definition(
            "Image banner",
            "Media",
            {
                "image": "",
                "alt": "Describe your image",
                "eyebrow": "",
                "heading": "Your heading",
                "body": "Add your content.",
                "button": "Explore",
                "link": "/",
                "secondary_button": "",
                "secondary_link": "",
                "overlay_opacity": 30,
                "content_position": "centre",
                "height": "medium",
                "text_colour": "#ffffff",
            },
            {
                **slide_fields,
                "eyebrow": text(),
                "secondary_button": text(),
                "secondary_link": text("url"),
                "overlay_opacity": number(0, 80, "%"),
                "content_position": positions,
                "height": height,
                "text_colour": {"kind": "colour"},
            },
        ),
        "before_after": definition(
            "Before and after",
            "Media",
            {
                "before_image": "",
                "before_alt": "Describe the first image",
                "before_label": "Before",
                "after_image": "",
                "after_alt": "Describe the second image",
                "after_label": "After",
                "split": 50,
                "orientation": "horizontal",
            },
            {
                "before_image": text("image"),
                "before_alt": text(),
                "before_label": text(),
                "after_image": text("image"),
                "after_alt": text(),
                "after_label": text(),
                "split": number(10, 90, "%"),
                "orientation": choice("horizontal", "vertical"),
            },
        ),
        "logo_cloud": definition(
            "Logo cloud",
            "Media",
            {"heading": "Your logos", "columns": 4, "greyscale": False, "items": [logo.copy()]},
            {"heading": text(), "columns": number(2, 8), "greyscale": {"kind": "toggle"}},
            items(logo, {"image": text("image"), "alt": text(), "link": text("url")}, 0, 24),
        ),
        "icon_box": definition(
            "Icon boxes",
            "Layout",
            {"columns": 3, "icon_position": "top", "alignment": "left", "items": [icon.copy()]},
            {"columns": number(1, 4), "icon_position": choice("top", "left"), "alignment": align},
            items(
                icon,
                {
                    "icon": choice(*ICON_PATHS),
                    "heading": text(),
                    "body": text("textarea"),
                    "link": text("url"),
                },
                1,
                12,
            ),
        ),
        "image_hotspots": definition(
            "Image hotspots",
            "Media",
            {"image": "", "alt": "Describe your image", "items": [spot.copy()]},
            {"image": text("image"), "alt": text()},
            items(
                spot,
                {
                    "x": number(0, 100, "%"),
                    "y": number(0, 100, "%"),
                    "heading": text(),
                    "body": text("textarea"),
                    "link": text("url"),
                },
                0,
                10,
            ),
        ),
        "section_heading": definition(
            "Section heading",
            "Text",
            {
                "eyebrow": "",
                "heading": "Your heading",
                "subheading": "Add your introduction.",
                "alignment": "left",
                "level": "h2",
            },
            {
                "eyebrow": text(),
                "heading": text(),
                "subheading": text("textarea"),
                "alignment": align,
                "level": choice("h1", "h2", "h3", "h4", "h5", "h6"),
            },
        ),
        "video_embed": definition(
            "Video embed",
            "Media",
            {"url": "", "poster": "", "aspect": "16/9"},
            {
                "url": text("url", max_length=2048, validate=validate_video_url),
                "poster": text("image"),
                "aspect": choice("16/9", "4/3", "1/1"),
            },
        ),
    }


def render_pack_widget(block, base="", preview=False):
    """Return a new pack section; the registry applies shared styling and hooks."""
    kind = block["type"]
    uid = "fs-pack-" + re.sub(r"[^a-zA-Z0-9_-]", "", str(block.get("id", "widget")))

    def field(key, field_kind="text"):
        return {"data_field": key, "data_field_type": field_kind} if preview else {}

    def image(value, alt, key="image", cls=""):
        return (
            Img(src=value, alt=alt, loading="lazy", cls=cls, **field(key, "image"))
            if value
            else Div(
                "Choose an image",
                role="img",
                aria_label=alt or "Image placeholder",
                cls="fs-widget-placeholder " + cls,
                **field(key, "image"),
            )
        )

    def href(value):
        return base + value if value.startswith("/") else value

    def link(label, value, cls="", key=None):
        return (
            A(label, href=href(value), cls=cls, **(field(key) if key else {}))
            if value and label
            else None
        )

    def heading(value, key="heading", level="h2"):
        return ft_hx(level, value, **field(key)) if value else None

    def banner(entry, index=None):
        prefix = f"items.{index}." if index is not None else ""
        return Div(
            image(
                entry.get("image", ""),
                entry.get("alt", ""),
                prefix + "image",
                "fs-pack-banner-image",
            ),
            Div(
                P(entry["eyebrow"], cls="fs-pack-eyebrow", **field(prefix + "eyebrow"))
                if entry.get("eyebrow")
                else None,
                heading(entry.get("heading", ""), prefix + "heading"),
                P(entry.get("body", ""), **field(prefix + "body")),
                Div(
                    link(
                        entry.get("button", ""),
                        entry.get("link", ""),
                        "fs-widget-button",
                        prefix + "button",
                    ),
                    link(
                        entry.get("secondary_button", ""),
                        entry.get("secondary_link", ""),
                        "fs-widget-button fs-widget-secondary",
                        "secondary_button",
                    ),
                    cls="fs-pack-actions",
                ),
                cls="fs-pack-banner-copy",
            ),
            cls="fs-pack-banner",
            data_position=block.get("text_position", block.get("content_position", "centre")),
            data_height=block.get("height", "medium"),
            style=f"--fs-overlay:{block.get('overlay_opacity', 30) / 100};color:{block.get('text_colour', '#ffffff')}",
        )

    children, attrs = [], {}
    if kind == "slideshow":
        slides = [
            Div(
                banner(item, i),
                cls="fs-pack-slide",
                role="group",
                aria_roledescription="slide",
                aria_label=f"{i + 1} of {len(block.get('items', []))}",
            )
            for i, item in enumerate(block.get("items", []))
        ]
        children = [
            Div(*slides, cls="fs-pack-slides"),
            Div(
                Button("Previous slide", type="button", data_slide_prev="", cls="fs-pack-control")
                if block.get("show_arrows", True)
                else None,
                Div(
                    *[
                        Button(
                            str(i + 1),
                            type="button",
                            data_slide_to=str(i),
                            aria_label=f"Show slide {i + 1}",
                            cls="fs-pack-dot",
                        )
                        for i in range(len(slides))
                    ],
                    cls="fs-pack-dots",
                )
                if block.get("show_dots", True)
                else None,
                Button("Next slide", type="button", data_slide_next="", cls="fs-pack-control")
                if block.get("show_arrows", True)
                else None,
                Button(
                    "Pause slideshow",
                    type="button",
                    data_slide_pause="",
                    aria_pressed="false",
                    cls="fs-pack-control",
                )
                if block.get("autoplay")
                else None,
                Span("", cls="fs-pack-sr", data_slide_status="", aria_live="polite"),
                cls="fs-pack-slide-controls",
                data_widget_control="",
                hidden=True,
            ),
        ]
        attrs = {
            "data_slideshow": "",
            "role": "region",
            "aria_label": "Slideshow",
            "data_autoplay": str(block.get("autoplay", False)).lower(),
            "data_interval": block.get("interval", 5),
        }
    elif kind == "image_banner":
        children = [banner(block)]
    elif kind == "before_after":
        children = [
            Div(
                *[
                    Div(
                        image(
                            block.get(side + "_image", ""),
                            block.get(side + "_alt", ""),
                            side + "_image",
                        ),
                        Span(
                            block.get(side + "_label", side.title()),
                            cls="fs-pack-compare-label",
                            **field(side + "_label"),
                        ),
                        cls="fs-pack-compare-" + side,
                    )
                    for side in ("before", "after")
                ],
                cls="fs-pack-compare-stage",
                data_widget_control="",
            ),
            Label(
                "Image comparison position",
                Input(
                    type="range",
                    min="10",
                    max="90",
                    value=str(block.get("split", 50)),
                    aria_label="Image comparison position",
                    data_compare_range="",
                ),
                cls="fs-pack-compare-control",
                data_widget_control="",
                hidden=True,
            ),
        ]
        attrs = {
            "data_before_after": "",
            "data_orientation": block.get("orientation", "horizontal"),
            "style": f"--fs-split:{block.get('split', 50)}%",
        }
    elif kind == "logo_cloud":
        logos = []
        for i, item in enumerate(block.get("items", [])):
            media = image(item.get("image", ""), item.get("alt", ""), f"items.{i}.image")
            logos.append(A(media, href=href(item["link"])) if item.get("link") else Div(media))
        children = [
            heading(block.get("heading", "")),
            Div(
                *logos,
                cls="fs-pack-logo-grid",
                style=f"--fs-pack-columns:{block.get('columns', 4)}",
                data_greyscale=str(block.get("greyscale", False)).lower(),
            ),
        ]
    elif kind == "icon_box":
        entries = []
        for i, item in enumerate(block.get("items", [])):
            svg = (
                '<svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                + ICON_PATHS.get(item.get("icon", "box"), ICON_PATHS["box"])
                + "</svg>"
            )
            entries.append(
                Div(
                    Span(NotStr(svg), cls="fs-pack-icon"),
                    Div(
                        heading(item.get("heading", ""), f"items.{i}.heading", "h3"),
                        P(item.get("body", ""), **field(f"items.{i}.body")),
                        link("Learn more", item.get("link", ""), "fs-pack-text-link"),
                    ),
                    cls="fs-pack-icon-entry",
                )
            )
        children = [
            Div(
                *entries,
                cls="fs-pack-icon-grid",
                style=f"--fs-pack-columns:{block.get('columns', 3)};text-align:{block.get('alignment', 'left').replace('centre', 'center')}",
                data_icon_position=block.get("icon_position", "top"),
            )
        ]
    elif kind == "image_hotspots":
        markers, details = [], []
        for i, item in enumerate(block.get("items", [])):
            target = uid + "-spot-" + str(i)
            markers.append(
                Button(
                    str(i + 1),
                    type="button",
                    cls="fs-pack-hotspot-marker",
                    style=f"left:clamp(22px,{item.get('x', 50)}%,calc(100% - 22px));top:clamp(22px,{item.get('y', 50)}%,calc(100% - 22px))",
                    aria_label="Show " + item.get("heading", "detail"),
                    aria_controls=target,
                    aria_expanded="false",
                    data_hotspot_target=target,
                    data_widget_control="",
                    hidden=True,
                )
            )
            details.append(
                Li(
                    heading(item.get("heading", ""), f"items.{i}.heading", "h3"),
                    P(item.get("body", ""), **field(f"items.{i}.body")),
                    link("Learn more", item.get("link", ""), "fs-pack-text-link"),
                    Button(
                        "Close detail",
                        type="button",
                        data_hotspot_close="",
                        data_widget_control="",
                        cls="fs-pack-control",
                        hidden=True,
                    ),
                    id=target,
                    cls="fs-pack-hotspot-detail",
                    data_widget_control="",
                    tabindex="-1",
                )
            )
        children = [
            Div(
                image(block.get("image", ""), block.get("alt", "")),
                *markers,
                cls="fs-pack-hotspot-stage",
            ),
            Ul(*details, cls="fs-pack-hotspot-details", aria_label="Image details"),
        ]
        attrs = {"data_hotspots": ""}
    elif kind == "section_heading":
        children = [
            P(block["eyebrow"], cls="fs-pack-eyebrow", **field("eyebrow"))
            if block.get("eyebrow")
            else None,
            heading(block.get("heading", ""), level=block.get("level", "h2")),
            P(block.get("subheading", ""), **field("subheading")),
        ]
        attrs = {
            "style": "text-align:" + block.get("alignment", "left").replace("centre", "center")
        }
    elif kind == "video_embed":
        source = video_embed_url(block.get("url", ""))
        children = [
            image(block["poster"], "Video poster", "poster", "fs-pack-video-poster")
            if block.get("poster")
            else None,
            Iframe(
                src=source,
                title="Video",
                loading="lazy",
                referrerpolicy="no-referrer",
                allow="fullscreen; picture-in-picture",
                allowfullscreen=True,
                cls="fs-pack-video-frame",
                style="aspect-ratio:" + block.get("aspect", "16/9"),
            )
            if source
            else Div("Choose a YouTube or Vimeo video", cls="fs-widget-placeholder"),
        ]
    else:
        return None
    return Section(*children, cls="fs-widget fs-pack fs-widget-" + kind, **attrs)
