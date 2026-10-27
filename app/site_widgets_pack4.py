"""Accessible showcase renderers and site-scoped catalogue fragments."""
from __future__ import annotations

import re

from fasthtml.common import A, Button, Div, Img, Li, NotStr, P, Section, Span, Ul, ft_hx

from app.services import money
from app.site_widgets_pack1 import ICON_PATHS
from app.site_widgets_pack2 import PLATFORM_LABELS
from app.site_widgets_pack2 import icon as social_icon
from app.site_widgets_pack3 import product_listing, scoped_products
from app.site_widgets_pack3 import validate_resources as validate_catalogue_resource
from app.site_widgets_pack4_definitions import (
    lightbox_source,
    pack_definitions,
    validate_pack_widget,
)

__all__ = ["pack_definitions", "validate_pack_widget", "render_pack_widget", "validate_resources", "product_cards"]


def validate_resources(db, site, block):
    if block["type"] == "shop_the_look":
        for item in block.get("items", []):
            validate_catalogue_resource(db, site, {"product": item.get("product", "")})
    return block


def _product_content(db, site, product):
    listing = product_listing(db, site, product)
    if listing is None:
        return []
    return [Img(src=product.image_url, alt=product.name, loading="lazy") if product.image_url else Div("Product image", cls="fs-widget-placeholder"),
        ft_hx("h3", product.name), P(money(listing.price_minor, listing.currency))]


def product_cards(db, site, ids, base=""):
    """Return only published site/channel products, preserving visitor order."""
    allowed = {product.id: product for product in scoped_products(db, site)}
    cards = []
    for identifier in dict.fromkeys(ids):
        product = allowed.get(identifier)
        if product is not None:
            content = _product_content(db, site, product)
            if content:
                cards.append(A(*content, Span("View product", cls="fs-pack4-product-link"), href=base + "/products/" + product.slug, cls="fs-pack4-product", data_product_id=product.id))
        if len(cards) == 8:
            break
    return cards


def _icon(name):
    return NotStr('<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICON_PATHS.get(name, ICON_PATHS["sparkle"]) + '</svg>')


def render_pack_widget(db, site, block, base="", preview=False):
    kind, items = block["type"], block.get("items", [])
    uid = "fs-pack4-" + re.sub(r"[^A-Za-z0-9_-]", "", str(block.get("id", "widget")))

    def field(key, field_kind="text"):
        return {"data_field": key, "data_field_type": field_kind} if preview else {}

    def heading(value, key="heading", level="h2"):
        return ft_hx(level, value, **field(key)) if value else None

    def image(value, alt, key="image", cls=""):
        return Img(src=value, alt=alt, loading="lazy", cls=cls, **field(key, "image")) if value else Div("Choose an image", cls="fs-widget-placeholder " + cls, role="img", aria_label=alt or "Image placeholder", **field(key, "image"))

    def href(value):
        return base + value if value.startswith("/") else value

    def button(label, value, style="primary", key="button_label"):
        return A(label, href=href(value), cls="fs-widget-button fs-widget-" + style, **field(key)) if label and value else None

    children, attrs = [], {}
    if kind == "marquee":
        def strip(clone=False):
            return Div(*[Span(Span(item["text"], **({} if clone else field(f"items.{i}.text"))), _icon(block["separator"]) if block.get("separator") else None, cls="marquee-item") for i, item in enumerate(items)], cls="marquee-items", **({"aria_hidden": "true"} if clone else {}))
        attrs = {"data_speed": block.get("speed", "medium"), "data_direction": block.get("direction", "left"), "data_marquee": ""}
        children = [Div(Div(strip(), strip(True), cls="marquee-track"), cls="marquee-viewport"), Button("Pause scrolling", type="button", data_marquee_toggle="", aria_pressed="false", hidden=True)]
    elif kind == "collage":
        tiles = []
        for i, item in enumerate(items):
            content = [image(item["image"], item["alt"], f"items.{i}.image"), heading(item.get("heading"), f"items.{i}.heading", "h3")]
            tiles.append(A(*content, href=href(item["link"]), cls="collage-tile") if item.get("link") else Div(*content, cls="collage-tile"))
        children = [Div(*tiles, cls="collage-grid", data_layout=block.get("layout", "large-two"))]
    elif kind == "flip_card":
        cards = []
        for i, item in enumerate(items):
            target = f"{uid}-back-{i}"
            cards.append(Div(Div(Div(image(item["image"], item["alt"], f"items.{i}.image") if item.get("image") else _icon(item["icon"]) if item.get("icon") else None,
                heading(item["heading"], f"items.{i}.heading", "h3"), cls="flip-card-front"),
                Div(P(item["body"], **field(f"items.{i}.body")), button(item.get("button_label"), item.get("link", ""), key=f"items.{i}.button_label"), id=target, cls="flip-card-back"), cls="flip-card-inner"),
                Button("Show details", type="button", aria_expanded="false", aria_controls=target, data_flip_toggle="", hidden=True), cls="flip-card"))
        children = [Div(*cards, cls="flip-grid")]
    elif kind == "cta_box":
        attrs = {"data_layout": block.get("layout", "inline"), "data_style": block.get("box_style", "bordered"), "data_has_image": str(bool(block.get("image"))).lower(), "style": f"--fs-pack4-overlay:{block.get('overlay_opacity', 60) / 100}"}
        children = [image(block["image"], block.get("alt", ""), cls="cta-background") if block.get("image") else None,
            Div(P(block["eyebrow"], cls="cta-eyebrow", **field("eyebrow")) if block.get("eyebrow") else None, heading(block["heading"]), P(block["body"], **field("body")), cls="cta-copy"),
            Div(button(block.get("button_label"), block.get("link", "")), button(block.get("secondary_button"), block.get("secondary_link", ""), "secondary", "secondary_button"), cls="cta-actions")]
    elif kind == "video_lightbox":
        source = lightbox_source(block.get("url", ""))
        attrs = {"data_video_src": source, "data_video_kind": "mp4" if source.startswith("/") else "iframe", "data_video_title": block.get("title", "Video")}
        children = [Div(image(block.get("poster", ""), block.get("alt", ""), "poster"),
            A("Play video", href=block["url"], data_video_open="", aria_haspopup="dialog", aria_controls=uid + "-dialog", cls="video-play") if source else P("Choose a video to play."), cls="video-poster"),
            ft_hx("dialog", heading(block.get("title", "Video"), "title"), Button("Close video", type="button", data_video_close=""), Div(data_video_mount=""), id=uid + "-dialog", data_video_dialog="", aria_label=block.get("title", "Video"))]
    elif kind == "team_members":
        people = []
        for i, item in enumerate(items):
            socials = []
            for j in range(1, 4):
                if item.get(f"social_{j}_url"):
                    platform = item.get(f"social_{j}_platform", "website")
                    label = PLATFORM_LABELS.get(platform, platform.capitalize())
                    socials.append(A(social_icon(platform), Span(label, cls="fs-pack4-sr"), href=item[f"social_{j}_url"], aria_label=item["name"] + ": " + label))
            people.append(Div(image(item["image"], item["alt"], f"items.{i}.image"), heading(item["name"], f"items.{i}.name", "h3"), P(item["role"], cls="team-role", **field(f"items.{i}.role")), P(item["bio"], **field(f"items.{i}.bio")), Div(*socials, cls="team-socials") if socials else None, cls="team-card"))
        children = [Div(*people, cls="team-grid", style=f"--fs-pack4-columns:{block.get('columns', 3)}")]
    elif kind == "pull_quote":
        attrs = {"data_style": block.get("quote_style", "plain")}
        children = [ft_hx("figure", ft_hx("blockquote", P(block["quote"], **field("quote"))),
            ft_hx("figcaption", Span(block["attribution"], **field("attribution")) if block.get("attribution") else None,
                ft_hx("cite", block["source"], **field("source")) if block.get("source") else None) if block.get("attribution") or block.get("source") else None)]
    elif kind == "button_group":
        children = [Div(*[button(item["label"], item["link"], item["button_style"], f"items.{i}.label") for i, item in enumerate(items)], cls="button-group", data_alignment=block.get("alignment", "left"), data_stack_mobile=str(block.get("stack_mobile", True)).lower(), data_full_width_mobile=str(block.get("full_width_mobile", True)).lower())]
    elif kind == "shop_the_look":
        products = {product.id: product for product in scoped_products(db, site)}
        markers, details, identifiers = [], [], []
        for i, item in enumerate(items):
            product = products.get(item.get("product"))
            if product is None:
                continue
            content = _product_content(db, site, product)
            if not content:
                continue
            identifiers.append(product.id)
            target = f"{uid}-spot-{i}"
            markers.append(Button(str(i + 1), type="button", cls="fs-pack-hotspot-marker", style=f"left:clamp(22px,{item['x']}%,calc(100% - 22px));top:clamp(22px,{item['y']}%,calc(100% - 22px))", aria_label="Show " + product.name, aria_controls=target, aria_expanded="false", data_hotspot_target=target, data_widget_control="", hidden=True))
            details.append(Li(*content, A("View product", href=base + "/products/" + product.slug), Button("Close detail", type="button", data_hotspot_close="", cls="fs-pack-control", hidden=True), id=target, cls="fs-pack-hotspot-detail", tabindex="-1"))
        if not identifiers and not preview:
            return None
        attrs = {"data_hotspots": ""}
        children = [heading(block.get("heading")), Div(Div(image(block.get("image", ""), block.get("alt", "")), *markers, cls="fs-pack-hotspot-stage"),
            Div(*product_cards(db, site, identifiers, base), cls="shop-the-look-products"), cls="shop-the-look-layout"),
            Ul(*details, cls="fs-pack-hotspot-details", aria_label="Look products"), Div("Choose products published on this site.", cls="fs-widget-placeholder") if not identifiers and preview else None]
    elif kind == "recently_viewed":
        attrs = {"data_recently_viewed": "", "data_site_id": site.id, "data_recently_viewed_url": base + "/widgets/recently-viewed", "data_limit": str(block.get("limit", 8)), "data_preview": str(preview).lower(), "hidden": not preview}
        children = [heading(block.get("heading")), Div("Products viewed on this site will appear here for each visitor.", cls="fs-widget-placeholder") if preview else None,
            Div(data_recently_viewed_content="", cls="fs-pack4-products", style=f"--fs-pack4-columns:{block.get('columns', 4)}")]
    else:
        return None
    return Section(*children, cls="fs-widget fs-pack4 fs-widget-" + kind + " widget-" + kind.replace("_", "-"), **attrs)
