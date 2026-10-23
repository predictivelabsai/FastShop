"""Server-rendered Liquid Metal components. Content comes from page blocks/settings."""

from __future__ import annotations

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
    Summary,
    Sup,
    Title,
    Ul,
)
from sqlalchemy import select

from app.commerce import discounted, payments_enabled, settings_for
from app.models import Product, ProductVariant, VariantChannelListing
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


def add_form(db, site, item, base, csrf, enabled, *, flavors=None, specs=None, compact=False):
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
        return A("View product", href=url(base, item["url"]), cls="metal-add-button bg-metal-sheen")
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
                    Span("Flavor" if i == 0 else "\u00a0", cls="metal-flavor-label"),
                    Span(v.name, cls="metal-flavor-value"),
                    cls="metal-flavor-column",
                )
                for i, v in enumerate(variants)
            ],
            role="group",
            aria_label="Flavor",
            cls="liquid-flavor-options",
        )
        if flavors
        else Input(type="hidden", name="variant_id", value=variants[0].id),
        Dl(*[Div(Dt(s["label"]), Dd(s["value"])) for s in specs], cls="metal-specs")
        if specs
        else None,
        Button("Add to cart", type="submit", cls="metal-add-button bg-metal-sheen"),
        action=base + "/cart/add",
        method="post",
        cls="metal-product-controls" + (" liquid-quick-add" if compact else ""),
    )


def render_block(db, site, page, config, b, base, csrf, enabled, preview=False):
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
                ),
                A(
                    b.get("button", "Shop"),
                    href=url(base, b.get("link", "/#products")),
                    cls="metal-cta bg-metal-sheen",
                ),
                P(config.get("footer", ""), cls="liquid-hero-disclaimer")
                if "*" in b.get("body", "") else None,
                cls="metal-container hero-copy",
            ),
            Button(
                "Ⅱ Pause",
                type="button",
                aria_label="Pause background motion",
                aria_pressed="false",
                data_liquid_motion="",
                cls="metal-hero-motion",
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
                            H3(s["heading"], cls="font-display"),
                            P(s["body"], cls="text-muted-foreground"),
                            P(b.get("body", ""), cls="liquid-card-disclaimer")
                            if "*" in s["heading"] or "*" in s["body"] else None,
                            cls="metal-card metal-benefit-card",
                        )
                        for s in items
                    ],
                    cls="liquid-benefits-grid",
                ),
                P(b.get("body", ""), cls="metal-benefits-disclaimer text-muted-foreground"),
                P(A(b["button"], href=url(base, b["link"])), cls="faq-more")
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
                P("Sample reviews", cls="liquid-sample-note"),
                Div(
                    Div(
                        *[
                            Figure(
                                photo(
                                    s["image"],
                                    "Fictional customer portrait placeholder",
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
                                ),
                                H3(s["heading"], cls="review-headline"),
                                Blockquote("“" + s["body"] + "”"),
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
                    aria_label="Sample reviews. Use left and right arrow keys.",
                    data_liquid_carousel="",
                ),
                Div(
                    Button("←", aria_label="Previous reviews", data_liquid_previous=""),
                    Div(
                        *[
                            Button(
                                Span(),
                                aria_label=f"Show reviews starting at {i + 1}",
                                aria_pressed="true" if i == 0 else "false",
                                data_liquid_dot=str(i),
                                cls="metal-review-dot",
                            )
                            for i in range(len(items))
                        ],
                        cls="liquid-dots",
                        role="group",
                        aria_label="Review position",
                    ),
                    Button("→", aria_label="Next reviews", data_liquid_next=""),
                    cls="metal-review-navigation",
                ),
                P(
                    "Reviews 1 of " + str(len(items)),
                    data_liquid_position="",
                    cls="sr-only",
                    aria_live="polite",
                ),
                cls="metal-container metal-section",
            ),
            id=b["id"],
            cls="metal-feature liquid-ruled",
            aria_roledescription="carousel",
            aria_label="Sample customer reviews",
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
                                Span("Read more →", cls="metal-learn-more"),
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
                    aria_label="Breadcrumb",
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
                                "4 flavors",
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
                                "View product",
                                href=url(base, s["url"]),
                                cls="shop-view text-primary",
                            ),
                            A(
                                "Choose flavor",
                                href=url(base, s["url"]),
                                cls="metal-add-button bg-metal-sheen",
                            )
                            if is_fizz
                            else add_form(db, site, s, base, csrf, enabled, compact=True),
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
            aria_label="All products",
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
                            ),
                            href=url(base, s["url"]),
                            cls="metal-card shop-choice",
                        )
                        for s in items
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
            aria_label="Shopping assurances",
        )
    from app.site_ui import render_section

    return render_section(db, site, page, config, b, base, csrf, preview)


def newsletter(shell, base, csrf, identity, enabled):
    offer = shell["offer"]
    return Form(
        Input(type="hidden", name="csrf_token", value=csrf),
        Div(
            Label("Email address", fr=identity, cls="sr-only"),
            Input(
                type="email",
                name="email",
                id=identity,
                placeholder=offer["placeholder"],
                required=True,
                maxlength=320,
                autocomplete="email",
                disabled=not enabled,
            ),
            Button(offer["cta"], type="submit", cls="bg-metal-sheen", disabled=not enabled),
            cls="liquid-email-row",
        ),
        Label(
            Input(type="checkbox", name="marketing_consent", required=True, disabled=not enabled),
            Span(offer["consent"] + " ", A(offer["privacyLabel"], href=offer["privacyHref"]), "."),
            cls="liquid-consent",
        ),
        Input(name="website", tabindex="-1", autocomplete="off", hidden=True),
        method="post",
        action=base + "/newsletter",
        cls="liquid-newsletter",
    )


def storefront(db, site, page, base, csrf, canonical, *, preview=False, message="", content=None, cart_count=0):
    config = (site.settings_json if preview else site.published_settings_json) or {}
    document = resolve_document(
        page.draft_json if preview else page.published_json, default_locale(site, preview=preview)
    )
    shell = config.get("liquid_shell", {})
    # The pack is reusable; incomplete settings do not break another site's preview.
    if not shell:
        from app.site_seed_liquid import CONTENT

        shell = {k: CONTENT[k] for k in ("offer", "footer", "nav")}
    enabled = payments_enabled(db, site, settings_for(db, site)) and not preview
    nav, footer, offer = shell["nav"], shell["footer"], shell["offer"]
    shop_links = rendered_navigation(db, site, "header", preview=preview)
    footer_links = rendered_navigation(db, site, "footer", preview=preview)
    shop_all = next(
        (s for s in shop_links if s["path"] == "/shop"), {"label": "Shop all", "path": "/shop"}
    )

    def logo():
        return Img(
            src=config.get("logo_light", "/static/h24you/liquid/logo-clean-light.png"),
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
    result = (
        Title(document["title"] if document["title"].endswith(" | " + site.name)
              else document["title"] + " | " + site.name),
        Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Meta(name="description", content=document.get("description", "")),
        Meta(
            name="robots",
            content="noindex,nofollow" if content is not None or preview or site.status != "published" else "index,follow",
        ),
        Meta(name="referrer", content="no-referrer") if content is not None else None,
        Meta(property="og:title", content=document["title"]),
        Meta(property="og:description", content=document.get("description", "")),
        Link(rel="canonical", href=canonical),
        Link(rel="icon", type="image/png", href="/static/h24you/liquid/logo-clean-dark.png"),
        faq_jsonld(document["blocks"]) if page.path == "/faq" else None,
        Link(rel="stylesheet", href=static_asset("site-theme-liquid.css")),
        Script(src=static_asset("site-liquid.js"), defer=True),
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
            A("Skip to content", href="#content", cls="liquid-skip"),
            Div(
                Div(
                    Div(
                        Span(offer["title"], cls="text-metal-sheen"),
                        Button(
                            "Request your code",
                            type="button",
                            data_liquid_offer_toggle="",
                            aria_expanded="false",
                            aria_controls="liquid-offer-form",
                            cls="metal-offer-request",
                        ),
                        Button(
                            "×",
                            type="button",
                            aria_label="Dismiss first order offer",
                            data_liquid_dismiss="",
                            cls="metal-offer-dismiss",
                        ),
                        cls="metal-offer-line",
                    ),
                    Div(
                        newsletter(shell, base, csrf, "offer-email", enabled),
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
                            A(icon("account"), href=base + "/account", aria_label="My account"),
                            A(
                                icon("cart"),
                                Span(
                                    str(cart_count), data_cart_count="", cls="liquid-cart-count bg-metal-sheen"
                                ),
                                href=base + "/cart",
                                data_cart_open="" if enabled else None,
                                aria_label="Open bag",
                                cls="liquid-cart",
                            ),
                            Button(
                                icon("menu"),
                                type="button",
                                aria_label="Open menu",
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
                ),
                cls="metal-top-bars",
            ),
            Div(
                Div(
                    logo(),
                    Button("×", type="button", aria_label="Close menu", data_liquid_menu_close=""),
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
            Footer(
                Div(
                    Div(
                        A(logo(), href=base + "/"),
                        H2(footer["newsletterTitle"], cls="liquid-footer-title"),
                        P(footer["newsletterBody"]),
                        newsletter(shell, base, csrf, "footer-email", enabled),
                    ),
                    Nav(
                        *[A(s["label"], href=url(base, s["path"])) for s in footer_links],
                        *[
                            A(s["label"], href=url(base, s["href"]))
                            for s in footer["links"]
                            if s["href"] != "/shop"
                        ],
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
                            config.get("email", "hello@h24you.com"),
                            href="mailto:" + config.get("email", "hello@h24you.com"),
                        ),
                        Div(
                            *[
                                A(
                                    icon(s["label"].lower()),
                                    href=s["href"],
                                    aria_label=site.name + " on " + s["label"],
                                )
                                for s in footer["social"]
                            ],
                            cls="liquid-socials",
                        ),
                        Div(
                            *[
                                Span(photo(
                                    "/static/h24you/liquid/" + filename,
                                    name, width=64, height=32,
                                ))
                                for name, filename in (
                                    ("Visa", "payment-visa.svg"),
                                    ("Mastercard", "payment-mastercard.svg"),
                                    ("PayPal", "payment-paypal.svg"),
                                    ("Apple Pay", "payment-applepay.svg"),
                                    ("Google Pay", "payment-google-pay-on-white.svg"),
                                )
                            ],
                            cls="metal-payment-logos",
                            aria_label="Payment methods",
                        ),
                    ),
                    cls="liquid-footer-grid",
                ),
                Div(
                    P(config.get("footer", "")),
                    P("© 2026 " + footer["company"] + ". All rights reserved."),
                    Small("Sandbox · Test purchases only") if enabled else None,
                    cls="liquid-footer-bottom",
                ),
                id="footer",
                cls="metal-footer",
            ),
            cls="h-site theme-metal",
            data_pack="liquid",
            data_tone="rose",
            data_site=site.id,
            data_builder_page=page.id if preview else "",
        ),
    )

    from app.liquid_icons import glyphs

    return glyphs(result)
