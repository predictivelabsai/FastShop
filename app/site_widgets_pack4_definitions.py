"""Showcase widget metadata and validation boundaries."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.services import CommerceError
from app.site_widgets_pack1 import ICON_PATHS, video_embed_url
from app.site_widgets_pack2 import SOCIAL_PATHS
from app.site_widgets_pack2_definitions import _social_url


def lightbox_source(value):
    """Only recognised video services or a site-hosted MP4 may be played."""
    if not value:
        return ""
    if isinstance(value, str) and re.fullmatch(r"/(?!/)[A-Za-z0-9_./%~-]+\.mp4", value, re.IGNORECASE):
        path = urlsplit(value).path
        if not any(part in {".", ".."} for part in path.split("/")) and "%" not in path:
            return value
    return video_embed_url(value)


def validate_video(value):
    lightbox_source(value)
    return value


def validate_pack_widget(block):
    if block["type"] == "team":
        for item in block.get("items", []):
            for i in range(1, 4):
                if item.get(f"social_{i}_url"):
                    _social_url(item[f"social_{i}_url"], item.get(f"social_{i}_platform", "website"))
    if block["type"] == "shop_the_look":
        for item in block.get("items", []):
            if not isinstance(item.get("product", ""), str):
                raise CommerceError("Choose a single product identifier.")
    return block


def pack_definitions(definition):
    def text(kind="text", **extra):
        return {"kind": kind, "max_length": 4000 if kind == "textarea" else 2048 if kind in {"image", "url", "video"} else 240, **extra}

    def choice(*values):
        return {"kind": "select", "options": values}

    def number(low, high):
        return {"kind": "range", "min": low, "max": high, "unit": ""}

    def items(default, fields, low, high):
        return {"defaults": default, "fields": fields, "min": low, "max": high, "required": tuple(fields)}

    toggle = {"kind": "toggle"}
    link = text("url", https=True, allow_relative=True)
    image = text("image", https=True, allow_relative=True)
    tile = {"image": "", "alt": "Describe your image", "heading": "Your heading", "link": ""}
    card = {"image": "", "alt": "Describe your image", "icon": "sparkle", "heading": "Your heading", "body": "Add the details you want to share.", "button_label": "Learn more", "link": ""}
    person = {"image": "", "alt": "Team member photo", "name": "Team member name", "role": "Role", "bio": "Add a short biography.", **{f"social_{i}_{key}": value for i in range(1, 4) for key, value in (("platform", "website"), ("url", ""))}}
    person_fields = {"image": image, "alt": text(), "name": text(), "role": text(), "bio": text("textarea", max_length=600), **{f"social_{i}_{key}": spec for i in range(1, 4) for key, spec in (("platform", choice(*SOCIAL_PATHS)), ("url", text("url")))}}
    button = {"label": "Your button", "link": "/", "button_style": "primary"}
    spot = {"x": 50, "y": 50, "product": ""}
    return {
        "marquee": definition("Marquee", "Marketing", {"items": [{"text": "Your announcement"}, {"text": "Add another short message"}], "separator": "sparkle", "speed": "medium", "direction": "left"}, {"separator": choice("", *ICON_PATHS), "speed": choice("slow", "medium", "fast"), "direction": choice("left", "right")}, items({"text": "Your message"}, {"text": text(max_length=160)}, 1, 10), icon="marquee", description="Scroll short messages with an accessible pause control"),
        "collage": definition("Collage", "Media", {"layout": "large-two", "items": [tile.copy() for _ in range(3)]}, {"layout": choice("large-two", "two-two", "large-four", "masonry")}, items(tile, {"image": image, "alt": text(), "heading": text(), "link": link}, 2, 8), icon="collage", description="Arrange images in a flexible mosaic with optional links"),
        "flip_card": definition("Flip cards", "Content", {"items": [card.copy()]}, {}, items(card, {"image": image, "alt": text(), "icon": choice("", *ICON_PATHS), "heading": text(), "body": text("textarea"), "button_label": text(), "link": link}, 1, 6), icon="flip-card", description="Reveal extra details on cards by pointer, tap or keyboard"),
        "cta_box": definition("Call to action box", "Marketing", {"eyebrow": "Your introduction", "heading": "Your call to action", "body": "Explain what visitors can do next.", "button_label": "Explore", "link": "/", "secondary_button": "", "secondary_link": "", "image": "", "alt": "", "overlay_opacity": 60, "layout": "inline", "box_style": "bordered"}, {"eyebrow": text(), "heading": text(), "body": text("textarea"), "button_label": text(), "link": link, "secondary_button": text(), "secondary_link": link, "image": image, "alt": text(), "overlay_opacity": number(0, 90), "layout": choice("inline", "stacked"), "box_style": choice("bordered", "filled")}, icon="cta-box", description="Pair a clear message with primary and secondary actions"),
        "video_lightbox": definition("Video lightbox", "Media", {"url": "", "poster": "", "alt": "Describe your video poster", "title": "Your video"}, {"url": text("url", https=True, allow_relative=True, validate=validate_video), "poster": image, "alt": text(), "title": text()}, icon="video-lightbox", description="Open a video on request in a keyboard-accessible dialog"),
        "team": definition("Team", "Content", {"columns": 3, "items": [person.copy()]}, {"columns": number(2, 4)}, items(person, person_fields, 1, 12), icon="team", description="Introduce your team with biographies and social links"),
        "pull_quote": definition("Pull quote", "Content", {"quote": "Add a quotation from your story.", "attribution": "", "source": "", "quote_style": "plain"}, {"quote": text("textarea"), "attribution": text(), "source": text(), "quote_style": choice("plain", "bordered", "accent")}, icon="pull-quote", description="Highlight an editorial quotation with optional attribution"),
        "button_group": definition("Button group", "Content", {"items": [button.copy(), {**button, "button_style": "secondary"}], "alignment": "left", "stack_mobile": True, "full_width_mobile": True}, {"alignment": choice("left", "centre", "right"), "stack_mobile": toggle, "full_width_mobile": toggle}, items(button, {"label": text(), "link": link, "button_style": choice("primary", "secondary", "link")}, 1, 4), icon="button-group", description="Group up to four links with responsive button styles"),
        "shop_the_look": definition("Shop the look", "Commerce", {"heading": "Shop the look", "image": "", "alt": "Describe your lifestyle image", "items": [spot.copy()]}, {"heading": text(), "image": image, "alt": text()}, items(spot, {"x": number(0, 100), "y": number(0, 100), "product": text("product", max_length=128)}, 0, 8), icon="shop-the-look", description="Link image hotspots to products published on this site"),
        "recently_viewed": definition("Recently viewed", "Commerce", {"heading": "Recently viewed", "limit": 8, "columns": 4}, {"heading": text(), "limit": number(1, 8), "columns": number(1, 4)}, icon="recently-viewed", description="Show products this visitor viewed on this site"),
    }
