"""Liquid product blocks, using the existing scoped catalogue and cart flow."""

from fasthtml.common import (
    H1,
    H2,
    H3,
    A,
    Article,
    Button,
    Caption,
    Dd,
    Del,
    Details,
    Div,
    Dl,
    Dt,
    Figure,
    Form,
    Img,
    Input,
    Label,
    Li,
    NotStr,
    Ol,
    P,
    Section,
    Small,
    Span,
    Summary,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Tr,
    Ul,
)
from sqlalchemy import select

from app.commerce import discounted
from app.models import Product, ProductVariant, VariantChannelListing
from app.services import money

ASSETS = "/static/h24you/liquid/"


def children(value):
    return value if isinstance(value, list) else [value]


def rows(b, theme):
    return [i for i in b.get("items", []) if i.get("theme") == theme]


def one(b, theme):
    return next(iter(rows(b, theme)), {})


def text(b, theme, field="body"):
    return one(b, theme).get(field, "")


def image(src, alt="", **attrs):
    return Img(src=src, alt=alt, loading="lazy", decoding="async", **attrs)


def name(value):
    from app.site_ui_liquid import product_name

    if "One" in value and not value.startswith("H2 | 4 YOU"):
        parts = value.split("One")
        return Span(parts[0], Span("One", cls="bottle-one"), parts[1])
    return product_name(value)


def icon(kind="water", benefit=False):
    from app.site_ui_liquid import icon as base_icon

    if benefit:
        return base_icon(kind, benefit=True)
    paths = {
        "shield": '<path d="m12 3 8 3v6c0 5-4 8-8 10-4-2-8-5-8-10V6Z"/><path d="m9 12 2 2 4-4"/>',
        "leaf": '<path d="M20 3C9 2 3 7 5 15c2 6 10 6 13 0 3-5 2-9 2-12ZM3 22c2-7 7-12 13-15"/>',
        "tested": '<path d="M9 3h6m-5 0v7L4 20q-1 2 2 2h12q3 0 2-2l-6-10V3M7 16h10"/>',
        "calories": '<circle cx="12" cy="12" r="9"/><text x="12" y="16" text-anchor="middle" font-size="11" fill="currentColor" stroke="none">0</text>',
        "sugar": '<rect x="6" y="6" width="12" height="12" rx="1.5"/><path d="m4 21 17-17"/>',
        "calendar": '<rect x="3" y="5" width="16" height="16" rx="2"/><path d="M7 2v6m8-6v6M3 11h16"/><circle cx="17" cy="17" r="5"/><path d="M17 14v3l2 1"/>',
    }
    if kind not in paths:
        return base_icon(kind)
    return NotStr(
        '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="liquid-icon">'
        + paths[kind]
        + "</svg>"
    )


def stars(rating):
    path = "M12 3 14.8 8.7 21 9.6 16.5 14 17.6 20.2 12 17.3 6.4 20.2 7.5 14 3 9.6 9.2 8.7Z"
    return NotStr(
        '<span class="product-stars text-primary" aria-label="'
        + f"{rating:.1f} out of 5 stars"
        + '">'
        + "".join(
            '<svg viewBox="0 0 24 24" width="18" height="18" fill="'
            + ("currentColor" if i < int(rating) else "none")
            + '" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="'
            + path
            + '"/></svg>'
            for i in range(5)
        )
        + "</span>"
    )


def specs(b):
    return Dl(
        *[Div(Dt(s["label"]), Dd(s["value"])) for s in rows(b, "spec")], cls="metal-specs mt-6"
    )


def disclosure(title, content, *, exclusive=False, technology=False):
    return Details(
        Summary(
            icon("shield") if technology else None,
            Span(title),
            Span("+", cls="disclosure-plus"),
            cls="bottle-disclosure-trigger",
        ),
        Div(content, cls="bottle-disclosure-copy text-muted-foreground"),
        cls="bottle-disclosure border-b",
        name="liquid-technology" if exclusive else None,
    )


def gallery(photos):
    return Div(
        Div(
            Div(
                Div(
                    *[
                        Figure(
                            Img(
                                src=p["image"],
                                alt=p.get("alt", ""),
                                width=1024,
                                height=1024,
                                loading="eager" if i == 0 else "lazy",
                            ),
                            cls="bottle-gallery-slide",
                        )
                        for i, p in enumerate(photos)
                    ],
                    cls="bottle-gallery-track",
                ),
                tabindex="0",
                cls="bottle-gallery-viewport metal-photo-frame",
            ),
            Div(
                Button(
                    "←",
                    type="button",
                    aria_label="Previous product photo",
                    data_gallery_previous="",
                    cls="liquid-outline",
                ),
                Button(
                    "→",
                    type="button",
                    aria_label="Next product photo",
                    data_gallery_next="",
                    cls="liquid-outline",
                ),
                cls="bottle-gallery-arrows",
            ),
            cls="relative",
        ),
        Div(
            *[
                Button(
                    image(p["image"], "", width=80, height=80),
                    type="button",
                    aria_label=f"View product photo {i + 1}",
                    aria_pressed="true" if i == 0 else "false",
                    data_gallery_index=str(i),
                    cls="liquid-outline",
                )
                for i, p in enumerate(photos)
            ],
            role="group",
            aria_label="Choose product photo",
            cls="bottle-thumbnails",
        ),
        P(f"Photo 1 of {len(photos)}", cls="sr-only", aria_live="polite", data_gallery_status=""),
        cls="bottle-gallery",
        aria_label="Product image gallery",
        aria_roledescription="carousel",
        data_product_gallery="",
    )


def catalogue(db, site, path):
    product = db.scalar(
        select(Product).where(
            Product.tenant_id == site.tenant_id,
            Product.slug == path.rstrip("/").split("/")[-1],
            Product.is_published.is_(True),
        )
    )
    if product is None:
        return []
    return list(
        db.execute(
            select(ProductVariant, VariantChannelListing.price_minor)
            .join(VariantChannelListing)
            .where(
                ProductVariant.product_id == product.id,
                ProductVariant.tenant_id == site.tenant_id,
                ProductVariant.is_active.is_(True),
                VariantChannelListing.channel_id == site.channel_id,
                VariantChannelListing.currency == "USD",
            )
            .order_by(ProductVariant.sort_order)
        )
    )


def quality(b):
    return Div(
        P(text(b, "quality-title"), cls="tablet-quality-title"),
        Ul(
            *[
                Li(
                    Span(
                        icon(
                            "shield"
                            if q["value"] == "gmp"
                            else "leaf"
                            if q["value"] == "gmo"
                            else q["value"]
                        ),
                        cls="tablet-quality-icon",
                    ),
                    Span(q["label"], cls="tablet-quality-label"),
                    data_placeholder="true",
                    cls="tablet-quality-item",
                )
                for q in rows(b, "quality")
            ],
            cls="tablet-quality-row",
        ),
        cls="tablet-quality",
    )


def flavor_options(b, variants, *, included=False):
    options = []
    for i, f in enumerate(rows(b, "flavor")):
        content = [
            Span(
                "Included" if included and i == 0 else "Flavor" if i == 0 else "\u00a0",
                cls="metal-flavor-label",
            ),
            Span(Span(cls="tablet-dot flavor-" + f["value"]), f["label"], cls="metal-flavor-value"),
        ]
        if included:
            options.append(Li(*content, cls="metal-flavor-column range-included-item"))
        elif i < len(variants):
            v, price = variants[i]
            options.append(
                Label(
                    Input(
                        type="radio",
                        name="variant_id",
                        value=v.id,
                        checked=i == 0,
                        cls="sr-only",
                        data_flavor=f["value"],
                        data_box_image=f.get("image", ""),
                        data_box_alt=f.get("alt", ""),
                        data_flavor_image=f.get("url", ""),
                        data_flavor_alt=f.get("body", ""),
                        data_unit_price=str(price),
                        data_subscription_price=str(discounted(price, 10)),
                    ),
                    *content,
                    cls="metal-flavor-column",
                )
            )
    return (Ul if included else Div)(
        *options,
        cls="metal-flavors tablet-flavors mt-6" + (" range-included" if included else ""),
        role="group",
        aria_label="Included" if included else "Flavor",
    )


def facts(b, mode):
    ingredients = (
        [
            P(s["body"], data_ingredient=s["value"], hidden=mode == "tablet" and i != 0, cls="mt-3")
            for i, s in enumerate(rows(b, "ingredient"))
        ]
        if mode == "tablet"
        else [
            P(Span("Unflavored: ", cls="range-facts-label"), text(b, "ingredient")),
            P(
                Span("Raspberry, Pineapple, Peach: ", cls="range-facts-label"),
                text(b, "ingredient-range"),
                cls="mt-2",
            ),
            P(text(b, "facts-note"), cls="mt-3 text-xs"),
        ]
    )
    return Div(
        P(text(b, "facts-lead"), cls="mb-3 font-semibold"),
        P(text(b, "facts-placeholder"), cls="text-xs font-semibold uppercase tracking-wide"),
        *ingredients,
        Table(
            Caption("Supplement Facts"),
            Thead(
                Tr(
                    Th("Per serving", scope="col"),
                    Th("Amount", scope="col"),
                    Th("% Daily Value", scope="col"),
                )
            ),
            Tbody(
                *[
                    Tr(Th(r["label"], scope="row"), Td(r["value"]), Td(r.get("body", "")))
                    for r in rows(b, "fact")
                ]
            ),
            cls="mt-4",
        ),
        data_placeholder="true",
        cls="tablet-facts",
    )


def stepper(minimum=1, value=1, hidden=False, field="quantity"):
    return Div(
        Button(
            "−",
            type="button",
            aria_label="Decrease quantity",
            data_quantity_step="-1",
            disabled=value <= minimum,
        ),
        Input(
            type="number",
            name=field,
            value=str(value),
            min=str(minimum),
            max="25",
            aria_label="Quantity",
            data_product_quantity="",
        ),
        Button("+", type="button", aria_label="Increase quantity", data_quantity_step="1"),
        role="group",
        aria_label="Quantity",
        cls="cart-stepper tablet-stepper",
        hidden=hidden,
        data_quantity_control="",
    )


def purchase(db, site, b, base, csrf, enabled):
    mode = b["layout"]
    variants = catalogue(db, site, b["url"])
    first = variants[0] if variants else (None, 0)
    unit = first[1]
    rating = one(b, "rating")
    summary = A(
        stars(float(rating.get("value", 0))),
        Span(f"{float(rating.get('value', 0)):.1f} · {rating.get('label', '0')} reviews (sample)"),
        href="#reviews",
        cls="product-rating mt-4 text-sm",
        data_sample="true",
    )
    controls = []
    if mode == "bottle":
        controls += [
            specs(b),
            P("Color: ", Span(text(b, "color", "label"), cls="font-semibold"), cls="mt-6 text-sm"),
            H2("Buy more, save more", cls="mt-7 font-display text-3xl"),
        ]
        controls.append(
            Div(
                *[
                    Label(
                        Input(
                            type="radio",
                            name="variant_id",
                            value=v.id,
                            checked=i == 0,
                            cls="sr-only",
                            data_bundle=str(i),
                            data_bundle_quantity=s["value"],
                            data_unit_price=str(price),
                            data_subscription_price=str(discounted(price, 10)),
                        ),
                        Span("-" + s["body"] + "%", cls="bottle-discount bg-metal-sheen")
                        if s["body"] != "0"
                        else None,
                        Span(s["label"], cls="font-semibold"),
                        Span(
                            money(price, "USD").removesuffix(".00"),
                            Small(" / bottle", cls="text-muted-foreground") if i else None,
                            cls="text-primary",
                        ),
                        cls="metal-card bottle-bundle",
                        data_bundle_option="",
                        aria_pressed="true" if i == 0 else "false",
                    )
                    for i, (s, (v, price)) in enumerate(
                        zip(rows(b, "bundle"), variants, strict=False)
                    )
                ],
                cls="bottle-bundles mt-4",
                role="group",
                aria_label="Bottle bundle",
            )
        )
        controls += [
            Div(
                Span("Quantity"),
                stepper(4, 4, field="custom_quantity"),
                cls="bottle-custom-quantity mt-4",
                hidden=True,
                data_custom_quantity="",
            ),
            Input(type="hidden", name="quantity", value="1", data_bottle_quantity=""),
            P(
                "Total ",
                Span(money(unit, "USD").removesuffix(".00"), data_product_total=""),
                Span("", cls="bottle-savings", data_product_savings="", hidden=True),
                aria_live="polite",
                cls="bottle-total mt-5 font-semibold",
            ),
            Button(
                "Add to cart",
                type="submit",
                disabled=not enabled or not variants,
                data_product_submit="",
                cls="metal-add-button mt-5 bg-metal-sheen",
            ),
        ]
    else:
        controls.append(flavor_options(b, variants, included=mode == "range"))
        if mode == "tablet":
            sub = one(b, "subscription")
            controls.append(
                Div(
                    Div(
                        P(sub.get("label", ""), cls="tablet-option-header"),
                        Label(
                            Input(type="radio", name="subscription", value="on", checked=True),
                            Span(
                                Span(
                                    Span(sub.get("heading", ""), cls="font-semibold"),
                                    Span(
                                        Del(
                                            money(unit, "USD"), cls="text-sm text-muted-foreground"
                                        ),
                                        " ",
                                        Span(
                                            money(discounted(unit, 10), "USD"), cls="text-primary"
                                        ),
                                    ),
                                    cls="tablet-option-title",
                                ),
                                Span(
                                    *[
                                        Span("✓ ", point)
                                        for point in sub.get("body", "").split("\n")
                                    ],
                                    cls="tablet-subscription-points mt-3 text-sm text-muted-foreground",
                                ),
                                cls="tablet-option-body",
                            ),
                            cls="tablet-option-row",
                        ),
                        cls="metal-card tablet-option",
                        data_selected="true",
                    ),
                    Div(
                        Span("Deliver every", cls="text-sm"),
                        Div(
                            Button("Month", type="button", aria_pressed="true"), cls="tablet-pills"
                        ),
                        cls="tablet-frequency",
                        data_product_frequency="",
                    ),
                    Div(
                        Label(
                            Input(type="radio", name="subscription", value="off"),
                            Span(
                                Span(text(b, "one-time", "label"), cls="font-semibold"),
                                Span(money(unit, "USD"), cls="text-primary"),
                                cls="tablet-option-title tablet-option-body",
                            ),
                            cls="tablet-option-row",
                        ),
                        cls="metal-card tablet-option",
                        data_selected="false",
                    ),
                    role="radiogroup",
                    aria_label="Purchase options",
                    cls="tablet-purchase-options mt-6",
                )
            )
            current = discounted(unit, 10)
        else:
            current = unit
            controls += [
                Input(type="hidden", name="variant_id", value=first[0].id if first[0] else ""),
                Div(
                    Del(money(11200, "USD"), cls="text-muted-foreground"),
                    Span(money(unit, "USD"), cls="text-2xl text-primary"),
                    Span(text(b, "pack"), cls="bottle-savings"),
                    cls="range-price mt-6",
                ),
                P(text(b, "pack", "label"), cls="mt-2 text-xs text-muted-foreground"),
            ]
        controls += [
            Div(
                stepper(),
                Button(
                    "Add to cart · ",
                    Span(money(current, "USD"), data_product_total=""),
                    disabled=not enabled or not variants,
                    type="submit",
                    data_product_submit="",
                    cls="metal-add-button bg-metal-sheen",
                ),
                cls="product-purchase-row mt-6",
            )
        ]
        if mode == "tablet":
            r = one(b, "range-link")
            controls += [
                P(text(b, "mix-hint"), cls="mt-3 text-xs text-muted-foreground"),
                P(
                    r.get("label", "") + " ",
                    A(
                        r.get("body", ""),
                        href=base + r.get("url", ""),
                        cls="text-primary underline",
                    ),
                    cls="mt-4 text-sm",
                ),
            ]
    controls.append(
        Ul(
            *[
                Li(
                    icon(
                        (
                            ("shipping", "calendar", "returns")
                            if mode == "tablet"
                            else ("shipping", "returns", "shield")
                        )[i % 3]
                    ),
                    Span(s["label"]),
                )
                for i, s in enumerate(rows(b, "trust"))
            ],
            cls="bottle-trust mt-6",
        )
    )
    if mode == "range":
        s = one(b, "subscribe-link")
        controls.append(
            P(
                A(s.get("body", ""), href=base + s.get("url", ""), cls="text-primary underline"),
                cls="mt-5 text-sm",
            )
        )
    controls.append(
        Div(
            *children(
                disclosure(
                    "What's in the box",
                    Ul(
                        *[Li(icon("box"), name(s["body"])) for s in rows(b, "box")],
                        cls="bottle-box-list",
                    ),
                )
                if mode == "bottle"
                else [
                    disclosure("Ingredients & Supplement Facts", facts(b, mode)),
                    disclosure("What's in the box", ", ".join(s["body"] for s in rows(b, "box"))),
                ]
            ),
            cls="mt-6 border-t",
        )
    )
    form = Form(
        Input(type="hidden", name="csrf_token", value=csrf),
        *controls,
        action=base + "/cart/add",
        method="post",
        data_product_purchase=mode,
        data_base_price=str(unit),
        data_product_name=b["heading"],
        cls="product-purchase-form",
    )
    panel = Div(
        P(b.get("eyebrow", ""), cls="text-sm text-muted-foreground"),
        Div(
            H1(name(b["heading"]), cls="font-display text-5xl leading-tight"),
            Span(money(unit, "USD").removesuffix(".00"), cls="text-xl text-primary")
            if mode == "bottle"
            else None,
            cls="product-title-row mt-3",
        ),
        summary,
        P(b.get("body", ""), cls="mt-6 leading-relaxed text-muted-foreground"),
        form,
        Div(
            Div(
                P(name(b["heading"]), cls="font-semibold"),
                P("", data_sticky_summary="", cls="text-xs text-muted-foreground"),
            ),
            Button("Add to cart", type="button", data_sticky_purchase="", cls="bg-metal-sheen"),
            cls="bottle-sticky-bar border-t",
            hidden=True,
            data_product_sticky="",
            aria_label="Quick purchase",
        )
        if mode != "range"
        else None,
        cls="bottle-purchase",
    )
    return Section(
        Div(
            Div(
                gallery(rows(b, "gallery")),
                quality(b) if mode != "bottle" else None,
                cls="tablet-gallery-col",
            ),
            panel,
            cls="metal-container metal-section bottle-top",
        ),
        cls="metal-light border-b liquid-product-section",
        id="tablet-product" if mode == "tablet" else b["id"],
    )


def fizz(index):
    glass = '<path class="fizz-glass" d="M30 20 L38 132 Q39 140 47 140 H93 Q101 140 102 132 L110 20"/><path class="fizz-water" d="M33 52 Q50 47 70 52 T107 52"/>'
    art = (
        '<rect class="fizz-tablet fizz-sink" x="58" y="108" width="24" height="9" rx="4"/>'
        if index == 0
        else '<rect class="fizz-tablet" x="58" y="126" width="24" height="7" rx="3.5"/>'
        if index == 1
        else '<ellipse class="fizz-glow" cx="70" cy="98" rx="34" ry="38"/>'
    )
    if index:
        for i, (x, y, r) in enumerate(
            [(62, 108, 7), (78, 96, 7), (68, 78, 7)]
            if index == 1
            else [
                (50, 120, 3),
                (86, 112, 4),
                (60, 96, 3.5),
                (80, 84, 3),
                (52, 74, 4),
                (92, 68, 3),
                (70, 104, 2.5),
                (66, 64, 3),
            ]
        ):
            art += (
                f'<g class="fizz-bubble" style="animation-delay:{i * 0.45}s"><circle cx="{x}" cy="{y}" r="{r}"/>'
                + (
                    f'<text x="{x}" y="{y + 2.6}" text-anchor="middle">H₂</text>'
                    if index == 1
                    else ""
                )
                + "</g>"
            )
    return NotStr(
        '<svg viewBox="0 0 140 150" aria-hidden="true" class="tablet-fizz-svg">'
        + glass
        + art
        + "</svg>"
    )


def render_product_block(db, site, b, base, csrf, enabled):
    kind = b["type"].removeprefix("liquid_product_")
    if not b["type"].startswith("liquid_product_"):
        return None
    if kind == "purchase":
        return purchase(db, site, b, base, csrf, enabled)
    title = H2(
        name(b.get("heading", "")) if kind in {"technology", "day"} else b.get("heading", ""),
        cls="font-display text-5xl",
    )
    body = None
    cls = "metal-container metal-section"
    if kind == "steps":
        bottle = b.get("layout", "").startswith("bottle")
        body = [
            title,
            Ol(
                *[
                    Li(
                        Div(
                            image(s["image"], s["alt"], width=1024, height=1024),
                            Span(str(i + 1)),
                            cls="bottle-step-photo",
                        )
                        if bottle
                        else None,
                        H3(s["heading"], cls="font-display text-3xl" + (" mt-6" if bottle else "")),
                        None if bottle else fizz(i),
                        P(
                            s["body"],
                            cls="mt-3 text-sm leading-relaxed text-muted-foreground"
                            if bottle
                            else "mt-4 text-sm leading-relaxed",
                        ),
                        cls="" if bottle else "tablet-fizz-moment",
                    )
                    for i, s in enumerate(b.get("items", []))
                ],
                cls=("bottle-steps" if bottle else "tablet-fizz") + " mt-12",
                data_liquid_reveal="",
            ),
            None
            if bottle
            else P(
                b.get("body", ""),
                cls="tablet-steps-chemistry text-center text-sm text-muted-foreground",
            ),
            None
            if bottle
            else P(
                A(b.get("button", ""), href=base + b.get("link", ""), cls="text-primary underline"),
                cls="mt-2 text-center text-sm",
            ),
        ]
        if not bottle:
            cls += " tablet-steps-section"
    elif kind == "technology":
        body = [
            title,
            Div(
                Div(
                    image(b["image"], b["alt"], width=768, height=768),
                    cls="metal-photo-frame bottle-tech-photo",
                ),
                Div(
                    *[
                        disclosure(s["heading"], s["body"], exclusive=True, technology=True)
                        for s in b.get("items", [])
                    ],
                    cls="bottle-detail-list",
                ),
                cls="bottle-technology mt-12",
            ),
            Div(icon("shield"), P(b.get("body", "")), cls="bottle-lab-strip mt-8"),
        ]
    elif kind == "day":
        source = one(b, "day-source")
        body = [
            title,
            Ol(
                *[
                    Li(
                        Div(
                            Span(s["label"], cls="font-accent italic text-xl"),
                            cls="bottle-day-time",
                        ),
                        Div(
                            image(s["image"], s["alt"], width=1024, height=1280),
                            Div(
                                P(s["value"], cls="font-display text-5xl"),
                                P(source.get("label", ""), cls="mt-2 text-sm leading-relaxed"),
                                P(source.get("body", ""), cls="mt-2 text-xs leading-relaxed"),
                                cls="bottle-day-overlay",
                            )
                            if s.get("value")
                            else None,
                            cls="metal-photo-frame bottle-day-photo",
                        ),
                        H3(s["heading"], cls="mt-5 font-semibold"),
                        P(
                            name(s["body"]),
                            cls="mt-2 text-sm leading-relaxed text-muted-foreground",
                        ),
                    )
                    for s in b.get("items", [])
                    if s.get("theme") in {"day", "day-stat"}
                ],
                cls="bottle-day-track mt-12",
                tabindex="0",
                aria_label="A day with the One timeline",
                data_liquid_reveal="",
            ),
        ]
    elif kind == "lifestyle":
        cls += " bottle-split"
        body = [
            Div(
                image(b["image"], b["alt"], width=768, height=1152),
                cls="metal-photo-frame bottle-lifestyle-photo",
            ),
            Div(
                title,
                P(
                    name(b.get("body", "")),
                    cls="mt-6 max-w-md leading-relaxed text-muted-foreground",
                ),
                A(
                    name(b["button"]),
                    href=base + b.get("link", ""),
                    cls="mt-6 inline-block text-primary underline",
                )
                if b.get("button")
                else None,
            ),
        ]
    elif kind == "flavors":
        tablet = b.get("layout", "").startswith("tablet")
        body = [
            title,
            Div(
                *[
                    Article(
                        Div(
                            image(
                                s["image"],
                                s["alt"],
                                width=1024,
                                height=1024,
                                cls="metal-learn-image",
                            ),
                            cls="metal-photo-frame",
                        ),
                        Div(
                            H3(
                                Span(cls="tablet-dot flavor-" + s["value"]),
                                s["heading"],
                                cls="font-display text-3xl",
                            ),
                            P(s["body"], cls="mt-2 text-sm leading-relaxed text-muted-foreground"),
                            Button(
                                "Select",
                                type="button",
                                data_select_flavor=s["value"],
                                aria_label="Select " + s["heading"],
                                cls="tablet-select mt-3 text-primary",
                            )
                            if tablet
                            else None,
                            cls="tablet-flavor-body",
                        ),
                        cls="metal-card tablet-flavor-card flavor-tint-" + s["value"],
                    )
                    for s in rows(b, "flavor")
                ],
                cls="tablet-flavor-grid mt-12",
            ),
            P(b.get("body", ""), cls="mt-10 text-center text-sm text-muted-foreground")
            if b.get("body")
            else None,
        ]
        if tablet:
            r = one(b, "range-link")
            body.append(
                P(
                    r.get("label", "") + " ",
                    A(
                        r.get("body", ""),
                        href=base + r.get("url", ""),
                        cls="text-primary underline",
                    ),
                    cls="mt-10 text-center text-sm",
                )
            )
    elif kind == "benefits":
        research = one(b, "research")
        benefits = [
            s for s in b.get("items", []) if s.get("theme") not in {"research", "research-cta"}
        ]
        body = [
            Div(
                title,
                Div(
                    NotStr('<svg class="tablet-benefit-lines" aria-hidden="true"></svg>'),
                    Div(image(b["image"], b["alt"]), cls="tablet-benefit-glass"),
                    *[
                        Div(
                            Span(icon(s["theme"], benefit=True), cls="tablet-benefit-icon"),
                            H3(s["heading"], cls="mt-3 font-display text-2xl"),
                            P(s["body"], cls="mt-2 text-sm leading-relaxed text-muted-foreground"),
                            P(
                                b.get("body", ""),
                                cls="mt-3 text-xs leading-relaxed text-muted-foreground benefit-disclaimer",
                            )
                            if "*" in s["heading"] or "*" in s["body"]
                            else None,
                            A(
                                s.get("label", ""),
                                href=base + s["url"],
                                cls="tablet-benefit-link mt-3 inline-block text-sm text-primary underline",
                            )
                            if s.get("url")
                            else None,
                            cls=f"tablet-benefit tablet-benefit-{i + 1}",
                            tabindex="0",
                        )
                        for i, s in enumerate(benefits)
                    ],
                    cls="tablet-benefit-map mt-12",
                ),
                P(
                    b.get("body", ""),
                    cls="product-disclaimer mt-12 text-center text-xs leading-relaxed text-muted-foreground",
                ),
                cls="tablet-benefits",
            ),
            Div(
                Div(
                    P(research.get("value", ""), cls="metal-stat font-display text-primary"),
                    P(research.get("label", ""), cls="mt-4 leading-relaxed"),
                    P(research.get("alt", ""), cls="mt-3 text-xs text-muted-foreground"),
                ),
                Div(
                    H2(research.get("heading", ""), cls="font-display text-5xl"),
                    P(research.get("body", ""), cls="mt-6 leading-relaxed text-muted-foreground"),
                    A(
                        text(b, "research-cta", "label"),
                        href=base + research.get("url", ""),
                        cls="metal-add-button tablet-research-cta mt-8 bg-metal-sheen",
                    ),
                ),
                cls="tablet-research mt-20 border-t pt-16",
            ),
        ]
    elif kind == "faq":
        cls += " product-faq-grid"
        body = [
            Div(
                title,
                P(
                    b.get("body", "") + " ",
                    A(
                        b.get("eyebrow", ""),
                        href="mailto:" + b.get("eyebrow", ""),
                        cls="text-primary underline",
                    ),
                    cls="mt-4 text-sm leading-relaxed text-muted-foreground",
                ),
            ),
            Div(
                *[
                    Details(
                        Summary(s["heading"], Span("+", cls="disclosure-plus")),
                        Div(
                            P(s["body"]),
                            A(
                                s.get("label", ""),
                                href=base + s["url"],
                                cls="text-primary underline text-sm",
                            )
                            if s.get("url")
                            else None,
                            cls="faq-answer text-muted-foreground",
                        ),
                        cls="product-faq border-b",
                        name="product-faq",
                        open=i == 0,
                    )
                    for i, s in enumerate(rows(b, "faq"))
                ],
                P(
                    A(b.get("button", ""), href=base + b.get("link", ""), cls="faq-more-link"),
                    cls="faq-more",
                ),
            ),
        ]
    elif kind == "pair":
        variants = catalogue(db, site, b["url"])
        product = one(b, "product")
        is_tablet = b.get("layout", "").startswith("tablet")
        controls = (
            flavor_options(b, variants)
            if is_tablet
            else [
                Input(
                    type="hidden", name="variant_id", value=variants[0][0].id if variants else ""
                ),
                specs(b),
            ]
        )
        form = Form(
            Input(type="hidden", name="csrf_token", value=csrf),
            Input(type="hidden", name="quantity", value="1"),
            Input(type="hidden", name="subscription", value="on" if is_tablet else "off"),
            *children(controls),
            Button(
                b.get("button", "Add to cart"),
                type="submit",
                disabled=not enabled or not variants,
                cls="metal-add-button bg-metal-sheen",
            ),
            action=base + "/cart/add",
            method="post",
            cls="metal-product-controls",
        )
        body = Div(
            Div(
                title,
                P(
                    name(b.get("body", "")),
                    cls="mt-6 max-w-sm leading-relaxed text-muted-foreground",
                ),
            ),
            Article(
                A(
                    image(b["image"], b["alt"], width=1280, height=1280, cls="metal-product-image"),
                    href=base + b["url"],
                    cls="metal-photo-frame",
                ),
                Div(
                    Div(
                        Div(
                            H3(
                                A(name(product.get("heading", "")), href=base + b["url"]),
                                cls="font-display text-3xl",
                            ),
                            P(
                                ("from " + money(discounted(variants[0][1], 10), "USD"))
                                if is_tablet and variants
                                else money(variants[0][1], "USD").removesuffix(".00")
                                if variants
                                else "",
                                cls="text-primary",
                            ),
                            cls="metal-product-title",
                        ),
                        P(b.get("eyebrow", ""), cls="mt-1 text-xs italic text-muted-foreground"),
                        P(product.get("body", ""), cls="metal-product-blurb text-muted-foreground"),
                        cls="metal-product-top",
                    ),
                    form,
                    cls="metal-product-body",
                ),
                cls="metal-card metal-product-card bottle-pair-card",
            ),
            cls="bottle-pair-panel bottle-pair-layout",
        )
        cls = "metal-container bottle-pair-spacing"
    if body is None:
        return None
    light = "light" in b.get("layout", "")
    return Section(
        Div(*children(body), cls=cls),
        cls=("metal-light " if light else "") + "border-b liquid-product-section",
        id=b["id"],
    )
