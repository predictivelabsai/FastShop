"""Real-catalogue commerce and progressively enhanced marketing widgets."""
from __future__ import annotations

import re

from fasthtml.common import A, Button, Div, Img, Li, NotStr, P, Section, Span, Ul, ft_hx
from sqlalchemy import select

from app.catalog_admin import collection_products
from app.content import site_pages
from app.discounts import status, usage
from app.models import (
    Collection,
    Discount,
    DiscountCode,
    Product,
    ProductVariant,
    VariantChannelListing,
)
from app.services import CommerceError, money, variant_price
from app.site_reviews import review_summary
from app.site_widgets_pack1 import ICON_PATHS
from app.site_widgets_pack3_definitions import pack_definitions, validate_pack_widget

__all__ = ["pack_definitions", "validate_pack_widget", "render_pack_widget", "active_discount_codes", "validate_resources", "scoped_products", "scoped_collections"]


def scoped_products(db, site):
    paths = {p.path for p in site_pages(db, site) if p.published_json}
    return list(db.scalars(select(Product).where(
        Product.tenant_id == site.tenant_id, Product.is_published.is_(True), Product.status == "active",
        ("/products/" + Product.slug).in_(paths),
        select(VariantChannelListing.id).join(ProductVariant).where(
            ProductVariant.product_id == Product.id, ProductVariant.tenant_id == site.tenant_id,
            ProductVariant.is_active.is_(True), VariantChannelListing.channel_id == site.channel_id).exists(),
    ).order_by(Product.name)))


def scoped_collections(db, site):
    ids = {p.collection_id for p in site_pages(db, site) if p.published_json and p.collection_id}
    return list(db.scalars(select(Collection).where(Collection.tenant_id == site.tenant_id,
        Collection.is_published.is_(True), Collection.id.in_(ids)).order_by(Collection.title)))


def active_discount_codes(db, site):
    rows = db.execute(select(DiscountCode, Discount).join(Discount, Discount.id == DiscountCode.discount_id).where(
        DiscountCode.tenant_id == site.tenant_id, DiscountCode.site_id == site.id,
        Discount.tenant_id == site.tenant_id, Discount.site_id == site.id,
        Discount.method == "code", Discount.deleted_at.is_(None), Discount.is_active.is_(True),
    ).order_by(DiscountCode.code)).all()
    return [code for code, rule in rows if status(rule) == "Active"
        and (rule.usage_limit is None or usage(db, rule) < rule.usage_limit)]


def validate_resources(db, site, block):
    if block.get("collection"):
        # A saved selection remains owned after unpublishing and can show its empty preview.
        ids = {page.collection_id for page in site_pages(db, site) if page.collection_id}
        collection = db.scalar(select(Collection.id).where(
            Collection.tenant_id == site.tenant_id, Collection.id == block["collection"],
            Collection.id.in_(ids)))
        if collection is None:
            raise CommerceError("Choose a collection belonging to this site.")
    if block.get("product") and block["product"] not in {row.id for row in scoped_products(db, site)}:
        raise CommerceError("Choose a product published on this site.")
    if block.get("discount_code"):
        # Existing saved codes may become inactive; ownership is permanent while display is live.
        code = db.scalar(select(DiscountCode).join(Discount).where(
            DiscountCode.id == block["discount_code"], DiscountCode.tenant_id == site.tenant_id,
            DiscountCode.site_id == site.id, Discount.tenant_id == site.tenant_id, Discount.site_id == site.id))
        if code is None:
            raise CommerceError("Choose a discount code belonging to this site.")
    return block


def _icon(name):
    return NotStr('<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICON_PATHS.get(name, ICON_PATHS["check"]) + '</svg>')


def product_listing(db, site, product):
    """Resolve the first active variant price in this site's channel."""
    variants = list(db.scalars(select(ProductVariant).where(ProductVariant.tenant_id == site.tenant_id,
        ProductVariant.product_id == product.id, ProductVariant.is_active.is_(True)).order_by(ProductVariant.id)))
    for variant in variants:
        try:
            return variant_price(db, variant.id, site.channel_id)
        except CommerceError:
            continue
    return None


def render_pack_widget(db, site, block, base="", preview=False):
    kind, items = block["type"], block.get("items", [])

    def field(key):
        return {"data_field": key, "data_field_type": "text"} if preview else {}

    def heading(value, key="heading", level="h2"):
        return ft_hx(level, value, **field(key)) if value else None

    def href(value):
        return base + value if value.startswith("/") else value

    def placeholder(text):
        return Section(Div(text, cls="fs-widget-placeholder"), cls="fs-widget fs-pack3 fs-widget-" + kind) if preview else None

    children, attrs = [], {}
    if kind == "featured_collection":
        collection = next((c for c in scoped_collections(db, site) if c.id == block.get("collection")), None)
        if not collection:
            return placeholder("Choose a published collection from this site.")
        allowed = {p.id for p in scoped_products(db, site)}
        products = [p for p in collection_products(db, site, collection) if p.id in allowed][:block.get("limit", 6)]
        cards = []
        for product in products:
            listing = product_listing(db, site, product)
            if listing:
                cards.append(A(Img(src=product.image_url, alt=product.name, loading="lazy") if product.image_url else Div("Product image", cls="fs-pack3-product-image-placeholder"),
                    ft_hx("h3", product.name), P(money(listing.price_minor, listing.currency)),
                    href=base + "/products/" + product.slug, cls="fs-pack3-product"))
        path = next((p.path for p in site_pages(db, site) if p.collection_id == collection.id and p.published_json), "")
        children = [Div(Img(src=collection.image_url, alt=collection.title, loading="lazy") if collection.image_url and block.get("show_image", True) else None,
            ft_hx("h2", collection.title), P(collection.description) if block.get("show_description", True) and collection.description else None,
            cls="fs-pack3-collection-heading"),
            Div(*cards, cls="fs-pack3-products", data_layout=block.get("layout", "grid"), style=f"--fs-pack3-columns:{block.get('columns', 3)}", tabindex="0" if block.get("layout") == "scroll" else None,
                role="region" if block.get("layout") == "scroll" else None, aria_label="Collection products" if block.get("layout") == "scroll" else None) if cards else P("Products will appear here when they are available."),
            A(block.get("link_label", "View all"), href=base + path, cls="fs-pack3-link", **field("link_label")) if path else None]
    elif kind == "pricing_table":
        plans = []
        for i, item in enumerate(items):
            plans.append(Div(P(item.get("badge", ""), cls="fs-pack3-badge", **field(f"items.{i}.badge")) if item.get("highlighted") and item.get("badge") else None,
                heading(item["name"], f"items.{i}.name", "h3"), P(item["price"], cls="fs-pack3-price", **field(f"items.{i}.price")),
                P(item["description"], **field(f"items.{i}.description")),
                Ul(*[Li(line.strip()) for line in item.get("features", "").splitlines() if line.strip()], **field(f"items.{i}.features")),
                A(item["button_label"], href=href(item["link"]), cls="fs-widget-button", **field(f"items.{i}.button_label")) if item.get("link") else None,
                cls="fs-pack3-plan", data_highlighted=str(item.get("highlighted", False)).lower()))
        children = [heading(block.get("heading")), Div(*plans, cls="fs-pack3-plans", style=f"--fs-pack3-columns:{len(items)}")]
    elif kind == "comparison_table":
        rows = []
        for i, item in enumerate(items):
            cells = []
            for j in range(1, block.get("options", 2) + 1):
                value = item.get(f"cell_{j}", "")
                cross = NotStr('<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg>')
                content = [_icon("check") if value.lower() == "yes" else cross, Span("Included" if value.lower() == "yes" else "Not included", cls="fs-pack3-sr")] if value.lower() in {"yes", "no"} else [value]
                cells.append(ft_hx("td", *content, **field(f"items.{i}.cell_{j}")))
            rows.append(ft_hx("tr", ft_hx("th", item["feature"], scope="row", **field(f"items.{i}.feature")), *cells))
        children = [Div(ft_hx("table", ft_hx("caption", block["caption"], **field("caption")),
            ft_hx("thead", ft_hx("tr", ft_hx("th", "Feature", scope="col"), *[ft_hx("th", block[f"option_{j}"], scope="col", **field(f"option_{j}")) for j in range(1, block.get("options", 2) + 1)])),
            ft_hx("tbody", *rows)), cls="fs-pack3-comparison-scroll", role="region", tabindex="0", aria_label="Comparison table, scroll horizontally to compare options")]
    elif kind == "trust_badges":
        children = [Div(*[Div(_icon(item["icon"]), Div(heading(item["heading"], f"items.{i}.heading", "h3"), P(item["text"], **field(f"items.{i}.text"))), cls="fs-pack3-trust-item") for i, item in enumerate(items)], cls="fs-pack3-trust", data_layout=block.get("layout", "row"))]
    elif kind == "announcement_bar":
        attrs = {"data_announcement": "", "data_scheme": block.get("scheme", "accent"), "data_interval": str(block.get("interval", 5)), "data_preview": str(preview).lower(), "data_storage_key": f"fastshop:announcement:{site.id}:{block.get('id', '')}"}
        children = [Div(*[Div(A(item["text"], href=href(item["link"]), **field(f"items.{i}.text")) if item.get("link") else Span(item["text"], **field(f"items.{i}.text")), data_announcement_message=str(i)) for i, item in enumerate(items)], cls="fs-pack3-announcements"),
            Button("Pause announcements", type="button", data_announcement_pause="", hidden=True) if len(items) > 1 else None,
            Button("Dismiss announcement", type="button", data_announcement_dismiss="", hidden=True) if block.get("dismissible") else None]
    elif kind == "promo_banner":
        attrs = {"data_has_image": str(bool(block.get("image"))).lower()}
        code = next((c for c in active_discount_codes(db, site) if c.id == block.get("discount_code")), None) if block.get("discount_code") else None
        children = [Div(heading(block["heading"]), P(block["body"], **field("body")),
            A(block["button_label"], href=href(block["link"]), cls="fs-widget-button", **field("button_label")) if block.get("link") else None,
            Div(ft_hx("code", code.code, data_promo_code="", tabindex="0"), Button("Copy code", type="button", data_copy_code="", hidden=True), Span("", role="status", aria_live="polite", data_copy_status=""), cls="fs-pack3-code") if code else None,
            cls="fs-pack3-promo-copy"), Img(src=block["image"], alt=block.get("image_alt", ""), loading="lazy", **field("image")) if block.get("image") else None]
    elif kind == "steps":
        children = [heading(block.get("heading")), ft_hx("ol", *[Li(Span(str(i + 1).zfill(2), cls="fs-pack3-step-number", aria_hidden="true"), _icon(item["icon"]) if item.get("icon") else None,
            heading(item["heading"], f"items.{i}.heading", "h3"), P(item["body"], **field(f"items.{i}.body"))) for i, item in enumerate(items)], cls="fs-pack3-steps")]
    elif kind == "star_rating":
        ids = {p.id for p in scoped_products(db, site)}
        product_id = block.get("product") if block.get("scope") == "product" else None
        if block.get("scope") == "product" and (not product_id or product_id not in ids):
            return placeholder("Choose a published product from this site.")

        count, average = review_summary(db, site, product_id, product_ids=ids)
        if not count:
            return placeholder("No approved reviews yet. Ratings appear only from approved customer reviews.")
        summary = f"Rated {average:.1f} out of 5 from {count} {'review' if count == 1 else 'reviews'}"
        children = [Span(_icon("star"), aria_hidden="true", cls="fs-pack3-stars"), P(summary), A(block.get("link_label", "Read reviews"), href=href(block["link"]), cls="fs-pack3-link", **field("link_label")) if block.get("link") else None]
    elif kind == "contact_card":
        children = [heading(block["heading"]), ft_hx("address", P(block["address"], cls="fs-pack3-address", **field("address")),
            A(block["phone"], href="tel:" + re.sub(r"[^+0-9]", "", block["phone"]), **field("phone")) if block.get("phone") else None,
            A(block["email"], href="mailto:" + block["email"], **field("email")) if block.get("email") else None,
            A("Open in maps", href=block["map_link"]) if block.get("map_link") else None),
            ft_hx("dl", *[Div(ft_hx("dt", item["day"], **field(f"items.{i}.day")), ft_hx("dd", item["hours"], **field(f"items.{i}.hours"))) for i, item in enumerate(items)], cls="fs-pack3-hours") if items else None]
    else:
        return None
    return Section(*children, cls="fs-widget fs-pack3 fs-widget-" + kind, **attrs)
