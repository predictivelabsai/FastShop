"""Server-rendered Liquid Metal components. Content comes from page blocks/settings."""

from __future__ import annotations

from datetime import UTC, datetime

from fasthtml.common import (
    H1,
    H2,
    H3,
    A,
    Article,
    Blockquote,
    Button,
    Dd,
    Del,
    Details,
    Div,
    Dl,
    Dt,
    Em,
    Figcaption,
    Figure,
    Footer,
    Form,
    Header,
    Img,
    Input,
    Label,
    Li,
    Link,
    Main,
    Meta,
    Nav,
    NotStr,
    P,
    Script,
    Section,
    Small,
    Span,
    Style,
    Summary,
    Sup,
    Title,
    Ul,
)
from sqlalchemy import select

from app.commerce import discounted, payments_enabled, settings_for
from app.models import Product, ProductVariant, SitePage, VariantChannelListing
from app.services import money
from app.site_blocks import default_locale, resolve_document
from app.site_menus import rendered_navigation
from app.site_ui import url
from app.ui import static_asset


def photo(src, alt="", **attrs):
    return Img(src=src, alt=alt, loading="lazy", decoding="async", **attrs)


def product_name(name):
    brand = "H2 | 4 YOU"
    if name.startswith(brand):
        return Span(
            Span(brand, cls="bottle-brand-name"),
            " ",
            Em(name[len(brand) :].strip(), cls="bottle-one"),
            cls="product-name",
        )
    return name


def catalog_price(db, site, path, *, subscription=False, quantity=1):
    """Read the same tenant/channel price as checkout, including its integer discount."""
    price = db.scalar(
        select(VariantChannelListing.price_minor)
        .join(ProductVariant)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(
            Product.tenant_id == site.tenant_id,
            Product.slug == path.rstrip("/").split("/")[-1],
            ProductVariant.tenant_id == site.tenant_id,
            ProductVariant.is_active.is_(True),
            VariantChannelListing.channel_id == site.channel_id,
            VariantChannelListing.currency == "USD",
            Product.is_published.is_(True),
        )
        .order_by(ProductVariant.sort_order, ProductVariant.id)
    )
    if price is None:
        return None
    price = discounted(price, 10) if subscription else price
    formatted = money(price * quantity, "USD")
    return formatted[:-3] if formatted.endswith(".00") else formatted


def display_price(db, site, item):
    is_fizz = item["url"].rstrip("/").endswith("/hydrogen-tablets")
    price = catalog_price(db, site, item["url"], subscription=is_fizz)
    return ("from " if is_fizz else "") + price if price else item.get("value", "")


def icon(kind, *, benefit=False):
    # Application-owned, fixed SVG paths. Merchant text is never executable markup.
    paths = {
        "cell": '<path d="M37 14c8 6 10 17 5 25s-17 11-26 5S5 29 10 19s18-12 27-5Z"/><circle cx="27" cy="28" r="7"/><path d="m17 18 3 2m16 17-3-2M14 32h3m17-14-2 3"/>',
        "shield": '<path d="m28 8 17 7v13c0 10-8 17-17 21-9-4-17-11-17-21V15Z"/><circle cx="24" cy="25" r="4"/><circle cx="35" cy="32" r="3"/><circle cx="21" cy="37" r="2"/><path d="m27 27 5 3m-9-1-1 6"/>',
        "exercise": '<path d="m18 36 18-18M12 29l15 15m2-32 15 15M8 33l15 15M33 8l15 15m-38 30 7 7m21-35 7 7"/>',
        "leaf": '<path d="M43 10C24 8 9 16 12 33c2 11 17 13 25 4 7-7 6-17 6-27ZM10 47c5-14 15-25 27-31M21 31l-1-9m7 3 9 1"/>',
        "molecule": '<circle cx="16" cy="28" r="10"/><circle cx="40" cy="28" r="10"/><path d="M26 26h4m-4 4h4M13 25v6m6-6v6m-6-3h6m18-3v6m6-6v6m-6-3h6M18 11c6-3 14-3 20 0M18 45c6 3 14 3 20 0"/>',
        "water": '<path d="M28 7C23 16 13 25 13 34a15 15 0 0 0 30 0c0-9-10-18-15-27ZM20 34a8 8 0 0 0 8 8m-3-22 3-4"/>',
        "account": '<circle cx="12" cy="8" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3"/>',
        "cart": '<path d="M3 3h2l2 13h12l3-9H6"/><circle cx="9" cy="21" r="1"/><circle cx="18" cy="21" r="1"/>',
        "instagram": '<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r="1"/>',
        "facebook": '<path d="M14 22v-9h3l.5-4H14V7c0-1 .5-2 2-2h2V1h-3c-4 0-6 2-6 6v2H6v4h3v9"/>',
        "menu": '<path d="M3 6h18M3 12h18M3 18h18"/>',
        "shipping": '<path d="M3 6h11v12H3zM14 10h4l3 4v4h-7"/><circle cx="7" cy="19" r="2"/><circle cx="18" cy="19" r="2"/>',
        "returns": '<path d="M4 8a9 9 0 1 1-1 8M4 2v6h6"/>',
        "box": '<path d="m12 3 9 5v10l-9 5-9-5V8Zm-9 5 9 5 9-5M12 13v10"/>',
    }
    view, size = ("0 0 56 56", 48) if benefit else ("0 0 24 24", 20)
    return NotStr(
        f'<svg viewBox="{view}" width="{size}" height="{size}" fill="none" '
        f'stroke="currentColor" stroke-width="1.3" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true" '
        f'class="{"metal-benefit-icon" if benefit else "liquid-icon"}">{paths.get(kind, paths["water"])}</svg>'
    )


def _field_attrs(preview, name, kind="text"):
    return {"data-field": name, "data-field-type": kind} if preview else {}


def _mark_nested_images(node, block, preview):
    """Expose repeater images through the same image picker path as block images."""
    if isinstance(node, (list, tuple)):
        for child in node:
            _mark_nested_images(child, block, preview)
        return
    if not preview or not hasattr(node, "children"):
        return
    for child in node.children:
        if getattr(child, "tag", "") == "img":
            src = child.attrs.get("src")
            alt = child.attrs.get("alt")
            for index, item in enumerate(block.get("items", [])):
                if src and src == item.get("image"):
                    child.attrs.update(_field_attrs(preview, f"items.{index}.image", "image"))
                    break
                if alt and alt == item.get("alt") and not child.attrs.get("data-field"):
                    child.attrs.update(_field_attrs(preview, f"items.{index}.alt"))
                    break
        _mark_nested_images(child, block, preview)


def _mark_liquid_fields(node, block, preview):
    """Bind bespoke renderer nodes to their source paths in canvas previews."""
    if isinstance(node, (list, tuple)):
        for child in node:
            _mark_liquid_fields(child, block, preview)
        return
    if not preview or not hasattr(node, "children"):
        return

    def text_of(element):
        if isinstance(element, str):
            return element
        return "".join(text_of(child) for child in getattr(element, "children", []))

    values = {}

    def collect(value, path=""):
        if isinstance(value, str) and path and value:
            values.setdefault(path, value)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                collect(item, f"{path}.{index}" if path else str(index))
        elif isinstance(value, dict):
            for key, item in value.items():
                if key not in {"id", "type", "role", "layout", "version", "hidden"}:
                    collect(item, f"{path}.{key}" if path else key)

    collect(block)

    def visit(element):
        if not hasattr(element, "attrs"):
            return
        tag = getattr(element, "tag", "")
        rendered = text_of(element).strip()
        for path, value in values.items():
            if path.endswith(".image") and tag == "img" and element.attrs.get("src") == value:
                element.attrs.update(_field_attrs(True, path, "image"))
            elif (path.endswith(".alt") or path == "portrait_alt") and tag == "img" and element.attrs.get("alt") == value:
                element.attrs["data-alt-field"] = path
            elif rendered == value:
                element.attrs.update(_field_attrs(True, path))
            elif path.endswith((".url", ".link", "link")) and tag == "a":
                href = element.attrs.get("href", "")
                if value and href.endswith(value):
                    element.attrs["data-link-field"] = path
        classes = element.attrs.get("class", "").split()
        if "shop-view" in classes:
            element.attrs.update(_field_attrs(True, "view_product"))
        if "faq-more" in classes:
            element.attrs.update(_field_attrs(True, "link"))
        if "metal-hero-motion" in classes:
            element.attrs.update(_field_attrs(True, "pause_label"))
        for child in getattr(element, "children", []):
            visit(child)

    visit(node)
    if block.get("type") == "liquid_trust" and hasattr(node, "attrs"):
        node.attrs.update(_field_attrs(True, "assurances_label"))


def add_form(db, site, item, base, csrf, enabled, *, flavors=None, specs=None, compact=False,
             labels=None, preview=False):
    labels = labels or {}
    product = db.scalar(
        select(Product).where(
            Product.tenant_id == site.tenant_id,
            Product.slug == item["url"].split("/")[-1],
            Product.is_published.is_(True),
        )
    )
    variants = (
        list(
            db.scalars(
                select(ProductVariant)
                .where(
                    ProductVariant.tenant_id == site.tenant_id,
                    ProductVariant.product_id == product.id,
                    ProductVariant.is_active.is_(True),
                )
                .order_by(ProductVariant.sort_order)
            )
        )
        if product
        else []
    )
    if not variants or not enabled:
        return A(labels.get("view_product", "View product"), href=url(base, item["url"]),
                 cls="metal-add-button bg-metal-sheen",
                 **_field_attrs(preview, "view_product"))
    return Form(
        Input(type="hidden", name="csrf_token", value=csrf),
        Input(type="hidden", name="quantity", value="1"),
        Input(type="hidden", name="subscription", value="on" if flavors else "off"),
        Div(
            *[
                Label(
                    Input(
                        type="radio", name="variant_id", value=v.id, checked=i == 0, cls="sr-only"
                    ),
                    Span(labels.get("flavor_label", "Flavor") if i == 0 else "\u00a0",
                         cls="metal-flavor-label",
                         **(_field_attrs(preview, "flavor_label") if i == 0 else {})),
                    Span(v.name, cls="metal-flavor-value"),
                    cls="metal-flavor-column",
                )
                for i, v in enumerate(variants)
            ],
            role="group",
            aria_label=labels.get("flavor_label", "Flavor"),
            cls="liquid-flavor-options",
        )
        if flavors
        else Input(type="hidden", name="variant_id", value=variants[0].id),
        Dl(*[Div(Dt(s["label"]), Dd(s["value"])) for s in specs], cls="metal-specs")
        if specs
        else None,
        Button(labels.get("add_to_cart", "Add to cart"), type="submit",
               cls="metal-add-button bg-metal-sheen",
               **_field_attrs(preview, "add_to_cart")),
        action=base + "/cart/add",
        method="post",
        cls="metal-product-controls" + (" liquid-quick-add" if compact else ""),
    )


def render_block(db, site, page, config, b, base, csrf, enabled, preview=False):
    from app.site_widgets import decorate_block, render_widget
    node = render_widget(db, site, page, config, b, base, csrf, preview,
                         lambda child: render_block(db, site, page, config, child, base, csrf, enabled, preview))
    if node is not None:
        _mark_nested_images(node, b, preview)
        _mark_liquid_fields(node, b, preview)
        return node
    node = _render_block(db, site, page, config, b, base, csrf, enabled, preview)
    node = decorate_block(node, b, preview)
    _mark_nested_images(node, b, preview)
    _mark_liquid_fields(node, b, preview)
    return node


def _render_block(db, site, page, config, b, base, csrf, enabled, preview=False):
    from app.site_ui_liquid_editorial import render_editorial
    from app.site_ui_liquid_products import render_product_block
    from app.site_ui_liquid_support import render_support

    content_block = render_support(b, base, csrf, not preview)
    if content_block is None:
        content_block = render_editorial(b, base)
    if content_block is not None:
        return content_block
    product_block = render_product_block(db, site, b, base, csrf, enabled)
    if product_block is not None:
        return product_block
    kind, heading = b["type"], b.get("heading", "")
    items = b.get("items", [])
    if kind == "liquid_hero":
        return Section(
            Div(
                Img(
                    src=b.get("image"),
                    alt="",
                    width=1920,
                    height=1024,
                    fetchpriority="high",
                    cls="animate-slow-zoom",
                ),
                cls="metal-hero-media",
                aria_hidden="true",
            ),
            Div(cls="metal-hero-shade", aria_hidden="true"),
            Div(
                H1(heading, cls="metal-hero-title font-display"),
                Div(cls="hero-rule"),
                P(
                    b.get("body", "").removesuffix("*"),
                    Sup("*") if b.get("body", "").endswith("*") else None,
                    cls="hero-subheadline",
                    data_field="body" if preview else None,
                    data_field_type="text" if preview else None,
                ),
                A(
                    b.get("button", "Shop"),
                    href=url(base, b.get("link", "/#products")),
                    cls="metal-cta bg-metal-sheen",
                ),
                P(b.get("disclaimer", config.get("footer", "")), cls="liquid-hero-disclaimer",
                  data_field="disclaimer" if preview else None,
                  data_field_type="text" if preview else None)
                if "*" in b.get("body", "") and b.get("disclaimer", config.get("footer", "")) else None,
                cls="metal-container hero-copy",
            ),
            Button(
                b.get("pause_label", "Ⅱ Pause"),
                Span(b.get("pause_aria_label", "Pause background motion"),
                     cls="sr-only", aria_hidden="true",
                     **_field_attrs(preview, "pause_aria_label")),
                type="button",
                aria_label=b.get("pause_aria_label", "Pause background motion"),
                aria_pressed="false",
                data_liquid_motion="",
                cls="metal-hero-motion",
                **_field_attrs(preview, "pause_label"),
            ),
            cls="metal-hero",
        )
    if kind == "liquid_stats":
        return Section(
            Div(
                Dl(
                    *[
                        Div(
                            Dt(s["label"], cls="sr-only"),
                            Dd(
                                s["value"],
                                cls="metal-stat font-display text-primary",
                                data_liquid_count=s["value"] if s["value"].endswith("+") else None,
                            ),
                            Div(cls="stat-rule"),
                            Dd(s["label"], cls="stat-label"),
                            Dd(s.get("body", ""), cls="metal-stat-note"),
                        )
                        for s in items
                    ],
                    cls="liquid-stats-grid",
                ),
                P(b.get("body", ""), cls="science-source"),
                cls="metal-container metal-section",
            ),
            id=b["id"],
            cls="metal-light liquid-ruled",
        )
    if kind == "liquid_collection":
        cards = []
        specs = [s for s in items if s.get("theme") == "spec"]
        flavors = [s for s in items if s.get("theme") == "flavor"]
        for item in (s for s in items if s.get("theme") == "product"):
            is_fizz = item["url"].rstrip("/").endswith("/hydrogen-tablets")
            cards.append(
                Article(
                    A(
                        photo(item["image"], item["alt"], cls="metal-product-image"),
                        href=url(base, item["url"]),
                        cls="metal-photo-frame",
                    ),
                    Div(
                        Div(
                            Div(
                                H3(
                                    A(product_name(item["heading"]), href=url(base, item["url"])),
                                    cls="font-display",
                                ),
                                P(display_price(db, site, item), cls="text-primary"),
                                cls="metal-product-title",
                            ),
                            P(item["label"], cls="product-subtitle"),
                            P(item["body"], cls="metal-product-blurb text-muted-foreground"),
                            cls="metal-product-top",
                        ),
                        add_form(
                            db,
                            site,
                            item,
                            base,
                            csrf,
                            enabled,
                            flavors=flavors if is_fizz else None,
                            specs=None if is_fizz else specs,
                            labels=b,
                            preview=preview,
                        ),
                        cls="metal-product-body",
                    ),
                    cls="metal-card metal-product-card",
                )
            )
        return Section(
            Div(
                H2(heading, cls="font-display text-5xl"),
                Div(
                    Div(
                        photo(b.get("image", ""), b.get("alt", ""), width=1280, height=1920),
                        A(
                            b.get("button", "Shop all"),
                            href=url(base, b.get("link", "/shop")),
                            cls="metal-collection-shop bg-metal-sheen",
                        ),
                        cls="metal-collection-lifestyle metal-photo-frame",
                    ),
                    Div(*cards, cls="metal-products-grid"),
                    cls="metal-collection-layout",
                ),
                cls="metal-container metal-section metal-collection-section",
            ),
            id=b["id"],
            cls="liquid-ruled",
        )
    if kind == "liquid_benefits":
        return Section(
            Div(
                H2(heading, cls="font-display text-5xl"),
                Div(
                    *[
                        Article(
                            icon(s["theme"], benefit=True),
                            H3(s["heading"], cls="font-display",
                               **_field_attrs(preview, f"items.{i}.heading")),
                            P(s["body"], cls="text-muted-foreground",
                              **_field_attrs(preview, f"items.{i}.body")),
                            P(b.get("body", ""), cls="liquid-card-disclaimer")
                            if "*" in s["heading"] or "*" in s["body"] else None,
                            cls="metal-card metal-benefit-card",
                        )
                        for i, s in enumerate(items)
                    ],
                    cls="liquid-benefits-grid",
                ),
                P(b.get("body", ""), cls="metal-benefits-disclaimer text-muted-foreground"),
                P(A(b["button"], href=url(base, b["link"]),
                    **_field_attrs(preview, "button")), cls="faq-more",
                  **_field_attrs(preview, "link"))
                if b.get("button") and b.get("link") else None,
                cls="metal-container metal-section metal-benefits-section",
            ),
            id=b["id"],
            cls="metal-light liquid-ruled",
        )
    if kind == "liquid_reviews":
        return Section(
            Div(
                H2(heading, cls="font-display text-5xl"),
                P(b.get("sample_note", "Sample reviews"), cls="liquid-sample-note"),
                Div(
                    Div(
                        *[
                            Figure(
                                photo(
                                    s["image"],
                                    b.get("portrait_alt", "Fictional customer portrait placeholder"),
                                    width=144,
                                    height=144,
                                    cls="metal-review-avatar",
                                    data_sample="true",
                                ),
                                Span(
                                    ("★" * int(s["value"]) + "☆" * (5 - int(s["value"])))
                                    if s["value"]
                                    else "",
                                    cls="review-stars",
                                    role="img",
                                    aria_label=s["value"] + " out of 5 stars",
                                    **_field_attrs(preview, f"items.{i}.value"),
                                ),
                                H3(s["heading"], cls="review-headline"),
                                Blockquote("“" + s["body"] + "”",
                                           **_field_attrs(preview, f"items.{i}.body")),
                                Figcaption(
                                    P(s["label"]),
                                    P(s.get("theme", ""), cls="review-location"),
                                    P(s.get("alt", ""), cls="review-product"),
                                ),
                                cls="metal-card metal-review-card",
                                data_sample="true",
                                aria_label=f"{i + 1} of {len(items)}",
                            )
                            for i, s in enumerate(items)
                        ],
                        cls="metal-review-track",
                    ),
                    cls="metal-review-viewport",
                    tabindex="0",
                    aria_label=b.get("carousel_label", "Sample reviews. Use left and right arrow keys."),
                    data_liquid_carousel="",
                    **_field_attrs(preview, "carousel_label"),
                ),
                Div(
                    Button("←", aria_label=b.get("previous_label", "Previous reviews"),
                           data_liquid_previous="", **_field_attrs(preview, "previous_label")),
                    Div(
                        *[
                            Button(
                                Span(),
                                aria_label=b.get("dot_label", "Show reviews starting at {number}").replace("{number}", str(i + 1)),
                                aria_pressed="true" if i == 0 else "false",
                                data_liquid_dot=str(i),
                                cls="metal-review-dot",
                                **_field_attrs(preview, "dot_label"),
                            )
                            for i in range(len(items))
                        ],
                        cls="liquid-dots",
                        role="group",
                        aria_label=b.get("position_label", "Review position"),
                        **_field_attrs(preview, "position_label"),
                    ),
                    Button("→", aria_label=b.get("next_label", "Next reviews"),
                           data_liquid_next="", **_field_attrs(preview, "next_label")),
                    cls="metal-review-navigation",
                ),
                P(
                    b.get("position_text", "Reviews {number} of {total}").replace("{number}", "1").replace("{total}", str(len(items))),
                    data_liquid_position="",
                    cls="sr-only",
                    aria_live="polite",
                    **_field_attrs(preview, "position_text"),
                ),
                cls="metal-container metal-section",
            ),
            id=b["id"],
            cls="metal-feature liquid-ruled",
            aria_roledescription="carousel",
            aria_label=b.get("reviews_label", "Sample customer reviews"),
            **_field_attrs(preview, "reviews_label"),
        )
    if kind == "liquid_articles":
        return Section(
            Div(
                H2(heading, cls="font-display text-5xl"),
                P(b.get("body", ""), cls="learn-intro text-muted-foreground"),
                Div(
                    *[
                        A(
                            Div(
                                photo(
                                    s["image"],
                                    s["alt"],
                                    width=1200,
                                    height=912,
                                    cls="metal-learn-image",
                                ),
                                cls="metal-photo-frame",
                            ),
                            Div(
                                P(s["label"], cls="metal-tag"),
                                H3(s["heading"], cls="font-display"),
                                P(s.get("body", ""), cls="metal-article-summary"),
                                Span(b.get("read_more", "Read more →"), cls="metal-learn-more"),
                                cls="metal-article-body",
                            ),
                            href=url(base, s["url"]),
                            cls="metal-card metal-article",
                        )
                        for s in items
                    ],
                    cls="liquid-articles-grid",
                ),
                cls="metal-container metal-section",
            ),
            id=b["id"],
            cls="metal-light home-learn",
        )
    if kind == "text" and b.get("layout") == "liquid-shop-heading":
        return Section(
            Div(
                Nav(
                    A(b.get("eyebrow", "Home"), href=base + "/"),
                    Span("/"),
                    Span(heading, aria_current="page"),
                    aria_label=b.get("breadcrumb_label", "Breadcrumb"),
                    cls="shop-breadcrumb",
                ),
                H1(heading, cls="font-display text-6xl"),
                P(b.get("body", ""), cls="shop-intro text-muted-foreground"),
                cls="metal-container",
            ),
            cls="shop-heading liquid-ruled",
        )
    if kind == "liquid_catalog":
        cards = []
        for s in items:
            is_fizz = s["url"].rstrip("/").endswith("/hydrogen-tablets")
            is_range = s["url"].rstrip("/").endswith("/taste-the-range")
            media = photo(s["image"], s["alt"], width=1024, height=1024)
            if is_range:
                media = Div(
                    *[
                        photo(
                            "/static/h24you/liquid/tablet-box-" + f + ".jpg",
                            "PLACEHOLDER: Hydrogen Tablets box, " + f,
                            width=1024,
                            height=1024,
                        )
                        for f in ("unflavoured", "raspberry", "pineapple", "peach")
                    ],
                    cls="shop-range-photo",
                )
            cards.append(
                Article(
                    A(
                        media,
                        href=url(base, s["url"]),
                        cls="metal-photo-frame shop-photo",
                        aria_label=s["heading"],
                    ),
                    Div(
                        P(s["label"], cls="shop-label text-muted-foreground"),
                        H2(
                            A(product_name(s["heading"]), href=url(base, s["url"])),
                            cls="shop-product-name",
                        ),
                        P(s["body"], cls="shop-description text-muted-foreground"),
                        Div(
                            Del(
                                catalog_price(db, site, "/products/hydrogen-tablets", quantity=4),
                                cls="text-muted-foreground",
                            )
                            if is_range
                            else None,
                            Span(display_price(db, site, s)),
                            cls="shop-price",
                        ),
                        Span(s["theme"], cls="shop-badge"),
                        Div(
                            Span(
                                b.get("flavour_count", "4 flavors"),
                                Span(
                                    *[
                                        Span(cls="tablet-dot flavor-" + f)
                                        for f in ("unflavored", "raspberry", "pineapple", "peach")
                                    ],
                                    cls="shop-flavor-dots",
                                ),
                                cls="shop-flavor-note text-muted-foreground",
                            )
                            if is_fizz
                            else A(
                                b.get("view_product", "View product"),
                                href=url(base, s["url"]),
                                cls="shop-view text-primary",
                                **_field_attrs(preview, "view_product"),
                            ),
                            A(
                                b.get("choose_flavour", "Choose flavor"),
                                href=url(base, s["url"]),
                                cls="metal-add-button bg-metal-sheen",
                            )
                            if is_fizz
                            else add_form(db, site, s, base, csrf, enabled, compact=True,
                                          labels=b, preview=preview),
                            cls="shop-actions",
                        ),
                        cls="shop-product-content",
                    ),
                    cls="metal-card shop-product",
                )
            )
        return Section(
            Div(Div(*cards, cls="shop-grid"), cls="metal-container shop-catalog"),
            cls="metal-light liquid-ruled",
            aria_label=b.get("products_label", "All products"),
        )
    if kind == "liquid_choices":
        return Section(
            Div(
                H2(heading, cls="font-display text-5xl"),
                Div(
                    *[
                        A(
                            H3(s["heading"], cls="font-display"),
                            P(s["body"], cls="text-muted-foreground"),
                            Span(
                                product_name(s["label"]),
                                Span("→", aria_hidden="true"),
                                cls="shop-choice-link text-primary",
                                **_field_attrs(preview, f"items.{i}.label"),
                            ),
                            href=url(base, s["url"]),
                            cls="metal-card shop-choice",
                        )
                        for i, s in enumerate(items)
                    ],
                    cls="shop-choices",
                ),
                cls="metal-container metal-section",
            ),
            cls="liquid-ruled",
        )
    if kind == "liquid_trust":
        return Section(
            Ul(
                *[
                    Li(icon(("shipping", "box", "returns")[i % 3]), Span(s["label"]))
                    for i, s in enumerate(items)
                ],
                cls="metal-container shop-trust",
            ),
            cls="metal-light liquid-ruled",
            aria_label=b.get("assurances_label", "Shopping assurances"),
            **_field_attrs(preview, "assurances_label"),
        )
    from app.site_ui import render_section

    return render_section(db, site, page, config, b, base, csrf, preview)


def newsletter(shell, base, csrf, identity, enabled, *, preview=False, footer=False):
    offer = shell["footer"] if footer else shell["offer"]
    field = (lambda name: "liquid_shell.footer.newsletter" + {
        "placeholder": "Placeholder", "emailLabel": "EmailLabel", "cta": "Cta",
        "consent": "Consent",
        "privacyLabel": "PrivacyLabel", "privacyHref": "PrivacyHref",
    }[name]) if footer else (lambda name: "liquid_shell.offer." + name)
    return Form(
        Input(type="hidden", name="csrf_token", value=csrf),
        Input(type="hidden", name="newsletter_source", value="footer" if footer else "offer"),
        P(offer["body"], **_field_attrs(preview, "liquid_shell.offer.body")) if not footer else None,
        Div(
            Label(offer.get("newsletterEmailLabel" if footer else "emailLabel", "Email address"),
                  fr=identity, cls="sr-only", **_field_attrs(preview, field("emailLabel"))),
            Input(
                type="email",
                name="email",
                id=identity,
                placeholder=offer["newsletterPlaceholder"] if footer else offer["placeholder"],
                required=True,
                maxlength=320,
                autocomplete="email",
                disabled=not enabled,
                **_field_attrs(preview, field("placeholder")),
            ),
            Button(offer["newsletterCta"] if footer else offer["cta"], type="submit",
                   cls="bg-metal-sheen", disabled=not enabled,
                   **_field_attrs(preview, field("cta"))),
            cls="liquid-email-row",
        ),
        Label(
            Input(type="checkbox", name="marketing_consent", required=True, disabled=not enabled),
            Span((offer["newsletterConsent"] if footer else offer["consent"]) + " ",
                 A(offer["newsletterPrivacyLabel"] if footer else offer["privacyLabel"],
                   href=url(base, offer["newsletterPrivacyHref"] if footer else offer["privacyHref"]),
                   **_field_attrs(preview, field("privacyLabel")))
                 if (offer["newsletterPrivacyHref"] if footer else offer["privacyHref"])
                 not in {"", "#", "/#"} else Span(offer["newsletterPrivacyLabel"]
                   if footer else offer["privacyLabel"],
                   **_field_attrs(preview, field("privacyLabel"))), ".",
                 **_field_attrs(preview, field("consent"))),
            cls="liquid-consent",
        ),
        Input(name="website", tabindex="-1", autocomplete="off", hidden=True),
        method="post",
        action=base + "/newsletter",
        cls="liquid-newsletter",
    )


def storefront(db, site, page, base, csrf, canonical, *, preview=False, editor=False, message="", content=None, cart_count=0):
    config = (site.settings_json if preview else site.published_settings_json) or {}
    document = resolve_document(
        page.draft_json if preview else page.published_json, default_locale(site, preview=preview)
    )
    from app.custom_data import resolve_dynamic_document
    from app.store_preferences import head_nodes
    from app.store_preferences import metadata as preference_metadata
    document = resolve_dynamic_document(db, site, page, document)
    preferences = preference_metadata(db, site, page, document)
    from app.site_tracking import product_item, tracking_nodes

    product = product_item(db, site, page) if not preview and content is None else None
    tracking_event = ({"name": "view_item", "currency": product["item"]["currency"],
                       "value": product["item"]["price"], "items": [product["item"]],
                       "variants": product["variants"]}
                      if product else {})
    provider_nodes = tracking_nodes(db, site, tracking_event,
                                    preview=preview or content is not None)
    from app.site_editable_settings import shell_fields, shell_values
    from app.site_liquid_shell import PAYMENT_MARKS

    # Merchant values from the visual editor; seed defaults fill absent paths only.
    resolved = shell_values({**config, "theme_pack": "liquid"}, shell_fields({"theme_pack": "liquid"}))
    shell, consent_copy = resolved["liquid_shell"], resolved["liquid_consent"]
    labels = shell["labels"]
    enabled = payments_enabled(db, site, settings_for(db, site)) and not preview
    payment_mode = "sandbox"
    if enabled:
        from app.commerce import payment_mode as effective_payment_mode
        payment_mode = effective_payment_mode(db, site)
    nav, footer, offer = shell["nav"], shell["footer"], shell["offer"]
    shop_links = rendered_navigation(db, site, "header", preview=preview)
    footer_links = rendered_navigation(db, site, "footer", preview=preview)
    footer_items = footer.get("links", [])
    old_footer_defaults = [
        (item.get("label"), item.get("href")) for item in footer_items
    ] == [
        ("Shop all", "/shop"), ("Terms and Conditions", "#"),
        ("Privacy Policy", "#"),
    ]
    policy_paths = {
        "/pages/privacy-policy", "/pages/terms-and-conditions",
        "/pages/returns-and-refunds", "/pages/shipping-policy",
    }
    published_policy_paths = set(db.scalars(select(SitePage.path).where(
        SitePage.site_id == site.id,
        SitePage.tenant_id == site.tenant_id,
        SitePage.published_json.is_not(None),
        SitePage.path.in_(policy_paths),
    )))
    if site.status == "published":
        published_policy_paths = policy_paths
    if old_footer_defaults:
        footer_items = [
            {"label": "Shop all", "href": "/shop"},
            {"label": "Privacy policy", "href": "/pages/privacy-policy"},
            {"label": "Terms and conditions", "href": "/pages/terms-and-conditions"},
            {"label": "Returns and refunds", "href": "/pages/returns-and-refunds"},
            {"label": "Shipping policy", "href": "/pages/shipping-policy"},
        ]
    footer_legal_links = [
        {"label": item["label"], "path": item["href"]}
        for item in footer_items
        if item.get("href") not in {"/shop", "", "#", "/#"}
        and (item.get("href") not in policy_paths or item.get("href") in published_policy_paths)
    ]
    shop_all = next(
        (s for s in shop_links if s["path"] == "/shop"), {"label": "Shop all", "path": "/shop"}
    )

    def logo():
        from app.site_editable_settings import shell_fields, shell_values
        shell_settings = shell_values({**config, "theme_pack": "liquid"},
                                      shell_fields({"theme_pack": "liquid"}))
        return Img(
            src=shell_settings["liquid_shell"]["header"]["logo"],
            alt=site.name,
            width=1774,
            height=262,
            cls="liquid-logo",
        )

    def group(g, mobile=False):
        links = [dict(s) for s in g["items"]]
        if g["label"] == nav["shop"]["label"] and links:
            links[0] = {"label": shop_all["label"], "href": shop_all["path"]}
        return Details(
            Summary(g["label"]),
            Ul(*[Li(A(product_name(s["label"]), href=url(base, s["href"]))) for s in links]),
            cls="liquid-mobile-group" if mobile else "liquid-dropdown",
        )

    blocks = []
    from app.site_ui_liquid_support import faq_jsonld

    for b in document["blocks"]:
        if b.get("hidden"):
            continue
        node = render_block(db, site, page, config, b, base, csrf, enabled, preview)
        if not editor:
            from app.site_widgets import strip_editor_hooks
            strip_editor_hooks(node)
        nodes = node if isinstance(node, (list, tuple)) else [node]
        for child in nodes:
            if preview and child is not None and hasattr(child, "attrs"):
                child.attrs.update({
                    "data-builder-section": b["id"],
                    "data-builder-label": (b.get("heading") or b["type"])[:100],
                })
            blocks.append(child)
    if content is not None:
        blocks = list(content)
    from app.site_theme import theme_style
    from app.site_ui import has_published_recently_viewed
    from app.site_widgets import (
        LIQUID_DESIGN_CSS,
        WIDGET_CSS,
        needs_pack1_css,
        needs_pack2_css,
        needs_pack2_js,
        needs_pack3_css,
        needs_pack3_js,
        needs_pack4_css,
        needs_pack4_js,
        needs_widget_css,
        needs_widget_js,
    )
    record_recent_product = bool(
        not preview
        and site.status == "published"
        and getattr(page, "kind", "") == "product"
        and getattr(page, "product_id", None)
        and has_published_recently_viewed(db, site)
    )
    privacy_href = consent_copy.get("privacy_href", "")
    # The banner always renders; only a policy link that would lead nowhere is left out.
    if privacy_href == "/pages/privacy-policy":
        has_privacy_policy = db.scalar(
            select(SitePage.id).where(
                SitePage.site_id == site.id,
                SitePage.tenant_id == site.tenant_id,
                SitePage.path == "/pages/privacy-policy",
            )
        ) is not None or bool((config.get("policies") or {}).get("privacy", "").strip())
    else:
        has_privacy_policy = privacy_href not in {"", "#"}
    copyright_line = labels.get("copyright") or (
        f"© {datetime.now(UTC).year} {footer['company']}. All rights reserved.")
    from app.site_editable_settings import shell_fields, shell_values
    shell_settings = shell_values({**config, "theme_pack": "liquid"},
                                  shell_fields({"theme_pack": "liquid"}))
    from app.site_seo import metadata as seo_metadata
    from app.site_seo import seo_document
    from app.site_ui_liquid_support import jsonld_for_page
    document = seo_document(db, site, page, document)
    seo = seo_metadata(site, page, document, canonical, preview=preview or content is not None)
    structured_data = [] if content is not None else jsonld_for_page(
        db, site, page, document, seo["canonical"], seo, preview=preview,
    )
    result = (
        Style(NotStr(WIDGET_CSS + LIQUID_DESIGN_CSS)) if needs_widget_css(document["blocks"]) or config.get("design") else None,
        Title(seo["title"]),
        *head_nodes(db, site, preferences, preview=preview or content is not None),
        *provider_nodes,
        Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Meta(name="description", content=seo["description"]),
        Meta(
            name="robots",
            content="noindex,nofollow" if content is not None or preview or site.status != "published" else "noindex" if seo["noindex"] else "index,follow",
        ),
        Meta(name="referrer", content="no-referrer") if content is not None else None,
        Meta(property="og:title", content=seo["social_title"]),
        Meta(property="og:description", content=seo["description"]),
        Meta(property="og:image", content=seo["image"]) if seo["image"] else None,
        Meta(property="og:type", content=seo["og_type"]),
        Meta(property="og:url", content=seo["canonical"]),
        Meta(name="twitter:card", content="summary_large_image" if seo["image"] else "summary"),
        Meta(name="google-site-verification", content=seo["google_verification"]) if seo["google_verification"] else None,
        Meta(name="msvalidate.01", content=seo["bing_verification"]) if seo["bing_verification"] else None,
        Link(rel="canonical", href=seo["canonical"]),
        *structured_data,
        Link(rel="icon", type="image/png", href=shell_settings["liquid_shell"]["header"]["favicon"]),
        faq_jsonld(document["blocks"]) if page.path == "/faq" else None,
        Link(rel="stylesheet", href=static_asset("site-theme-liquid.css")),
        Script(src=static_asset("site-liquid.js"), defer=True),
        Link(rel="stylesheet", href=static_asset("site-liquid-consent.css")),
        Script(src=static_asset("site-liquid-consent.js"), defer=True),
        Link(rel="stylesheet", href=static_asset("widgets-pack1.css")) if needs_pack1_css(document["blocks"]) else None,
        Script(src=static_asset("widgets-pack1.js"), defer=True) if needs_widget_js(document["blocks"]) else None,
        Link(rel="stylesheet", href=static_asset("widgets-pack2.css")) if needs_pack2_css(document["blocks"]) else None,
        Script(src=static_asset("widgets-pack2.js"), defer=True) if needs_pack2_js(document["blocks"]) else None,
        Link(rel="stylesheet", href=static_asset("widgets-pack3.css")) if needs_pack3_css(document["blocks"]) else None,
        Script(src=static_asset("widgets-pack3.js"), defer=True) if needs_pack3_js(document["blocks"]) else None,
        Link(rel="stylesheet", href=static_asset("widgets-pack4.css")) if needs_pack4_css(document["blocks"]) else None,
        Script(src=static_asset("widgets-pack4.js"), defer=True)
        if needs_pack4_js(document["blocks"]) or record_recent_product else None,
        Script(src=static_asset("customer.js"), defer=True) if content is not None else None,
        Link(rel="stylesheet", href=static_asset("site-theme-liquid-products.css")),
        Script(src=static_asset("site-liquid-products.js"), defer=True),
        Link(rel="stylesheet", href=static_asset("site-theme-liquid-support.css")),
        Script(src=static_asset("site-liquid-support.js"), defer=True),
        Link(rel="stylesheet", href=static_asset("site-theme-liquid-editorial.css")),
        Script(src=static_asset("site-liquid-editorial.js"), defer=True),
        Link(rel="stylesheet", href=static_asset("cart-drawer.css")) if enabled else None,
        Script(src=static_asset("cart-drawer.js"), defer=True) if enabled else None,
        Link(rel="stylesheet", href=static_asset("site-preview.css")) if preview else None,
        Script(src=static_asset("site-preview.js"), defer=True) if preview else None,
        Div(
            A(labels["skip"], href="#content", cls="liquid-skip"),
            Div(
                Div(
                    Div(
                        Span(offer["title"], cls="text-metal-sheen"),
                        Button(
                            labels["request"],
                            type="button",
                            data_liquid_offer_toggle="",
                            aria_expanded="false",
                            aria_controls="liquid-offer-form",
                            cls="metal-offer-request",
                        ),
                        Button(
                            "×",
                            type="button",
                            aria_label=labels["dismiss"],
                            data_liquid_dismiss="",
                            cls="metal-offer-dismiss",
                        ),
                        cls="metal-offer-line",
                    ),
                    Div(
                        newsletter(shell, base, csrf, "offer-email", enabled, preview=preview),
                        id="liquid-offer-form",
                        hidden=True,
                        cls="liquid-offer-form",
                    ),
                    cls="metal-offer",
                    data_liquid_offer="",
                ),
                Header(
                    Div(
                        A(logo(), href=base + "/"),
                        Nav(
                            Ul(
                                Li(group(nav["shop"])),
                                Li(group(nav["why"])),
                                Li(A(nav["about"]["label"], href=url(base, nav["about"]["href"]))),
                            ),
                            aria_label="Main",
                        ),
                        Div(
                            A(icon("account"), href=base + "/account", aria_label=labels["account"]),
                            A(
                                icon("cart"),
                                Span(
                                    str(cart_count), data_cart_count="", cls="liquid-cart-count bg-metal-sheen"
                                ),
                                href=base + "/cart",
                                data_cart_open="" if enabled else None,
                                aria_label=labels["bag"],
                                cls="liquid-cart",
                            ),
                            Button(
                                icon("menu"),
                                type="button",
                                aria_label=labels["menu"],
                                aria_expanded="false",
                                aria_controls="liquid-mobile-menu",
                                data_liquid_menu="",
                                cls="liquid-menu-toggle",
                            ),
                            cls="liquid-header-actions",
                        ),
                        cls="liquid-header-row",
                    ),
                    cls="metal-header",
                    data_builder_shell="header",
                ),
                cls="metal-top-bars",
            ),
            Div(
                Div(
                    logo(),
                    Button("×", type="button", aria_label=labels["close"], data_liquid_menu_close=""),
                    cls="liquid-mobile-top",
                ),
                Nav(
                    group(nav["shop"], True),
                    group(nav["why"], True),
                    A(nav["about"]["label"], href=url(base, nav["about"]["href"])),
                    aria_label="Mobile",
                ),
                Nav(
                    *[A(s["label"], href=url(base, s["href"])) for s in nav["helpLinks"]],
                    aria_label="Help",
                    cls="liquid-mobile-help",
                ),
                id="liquid-mobile-menu",
                hidden=True,
                role="dialog",
                aria_modal="true",
                aria_label="Menu",
                cls="liquid-mobile-menu",
            ),
            P(message, role="status", cls="liquid-message") if message else None,
            Main(
                *blocks,
                id="content",
                tabindex="-1",
                cls=("liquid-storefront-page metal-light" if content is not None else
                     "shop-page" if page.path == "/shop" else
                     "about-page metal-light" if page.path == "/about" else
                     "faq-page metal-light" if page.path == "/faq" else
                     "contact-page metal-light" if page.path == "/contact" else
                     "why-page" if page.path == "/why-hydrogen" else
                     "why-page why-science" if page.path == "/why-hydrogen/science" else
                     "bottle-page tablet-page range-page" if page.path == "/products/taste-the-range" else
                     "bottle-page tablet-page" if page.path == "/products/hydrogen-tablets" else
                     "bottle-page" if page.path == "/products/h2-4-you-one" else ""),
            ),
            Div(hidden=True, data_recent_product=page.product_id, data_site_id=site.id,
                data_required_consent="preferences") if record_recent_product else None,
            Footer(
                Div(
                    Div(
                        A(logo(), href=base + "/"),
                        H2(footer["newsletterTitle"], cls="liquid-footer-title",
                           **_field_attrs(preview, "liquid_shell.footer.newsletterTitle")),
                        P(footer["newsletterBody"],
                          **_field_attrs(preview, "liquid_shell.footer.newsletterBody", "textarea")),
                        newsletter(shell, base, csrf, "footer-email", enabled,
                                  preview=preview, footer=True),
                    ),
                    Nav(
                        *[A(s["label"], href=url(base, s["path"])) for s in footer_links],
                        H3(footer["legalTitle"]) if footer_legal_links else None,
                        *[A(item["label"], href=url(base, item["path"])) for item in footer_legal_links],
                        H3(footer["helpTitle"]),
                        *[
                            A(
                                s["label"],
                                href=url(base, s["href"])
                                + ("#" + s["hash"] if s.get("hash") else ""),
                            )
                            for s in footer["helpLinks"]
                        ],
                        aria_label="Footer",
                    ),
                    Div(
                        P(footer["company"]),
                        P(footer["address"]),
                        A(
                            resolved["email"],
                            href="mailto:" + resolved["email"],
                        ) if resolved["email"] else None,
                        Div(
                            *[
                                A(
                                    icon(s["label"].lower()),
                                    href=s["href"],
                                    aria_label=site.name + " on " + s["label"],
                                )
                                for s in footer["social"] if s["href"] not in {"", "#", "/#"}
                            ],
                            cls="liquid-socials",
                        ),
                        Div(
                            *[
                                Span(photo(
                                    "/static/h24you/liquid/" + PAYMENT_MARKS[name],
                                    name, width=64, height=32,
                                ))
                                for name in footer.get("payments", []) if name in PAYMENT_MARKS
                            ],
                            cls="metal-payment-logos",
                            aria_label="Payment methods",
                        ),
                    ),
                    cls="liquid-footer-grid",
                ),
                Div(
                    P(resolved["footer"]),
                        Button(labels["cookies"], type="button", data_cookie_open=""),
                    P(copyright_line),
                    Small("Sandbox · Test purchases only") if enabled and payment_mode != "live" else None,
                    cls="liquid-footer-bottom",
                ),
                id="footer",
                cls="metal-footer",
                data_builder_shell="footer",
            ),
            Div(
                H2(consent_copy["heading"]),
                P(consent_copy["body"]),
                Details(
                    Summary(consent_copy["categories"]),
                    Label(Input(type="checkbox", checked=True, disabled=True), " " + consent_copy["essential"]),
                    Label(Input(type="checkbox", id="consent-preferences"), " " + consent_copy["preferences"]),
                    Label(Input(type="checkbox", id="consent-analytics"), " " + consent_copy["analytics"]),
                    Label(Input(type="checkbox", id="consent-marketing"), " " + consent_copy["marketing"]),
                ),
                Div(
                    Button(consent_copy["accept"], type="button", data_consent="all"),
                    Button(consent_copy["decline"], type="button", data_consent="none"),
                    Button(consent_copy["save"], type="button", data_consent="custom"),
                    cls="liquid-cookie-actions",
                ),
                A(consent_copy["privacy_label"], href=url(base, privacy_href))
                if has_privacy_policy else None,
                id="h-cookie",
                cls="liquid-cookie",
                role="region",
                aria_label="Cookie preferences",
                hidden=True,
                data_builder_shell="consent",
                data_preview_consent="true" if preview else None,
            ),
            cls="h-site theme-metal",
            style=theme_style(config) or None, data_themed="true" if theme_style(config) else None,
            data_pack="liquid",
            data_tone="rose",
            data_site=site.id,
            data_builder_page=page.id if preview else "",
        ),
    )

    from app.liquid_icons import glyphs

    return glyphs(result)
