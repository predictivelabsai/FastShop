"""Catalogue presentation using the shared merchant shell and components."""

import json
from html import escape

from fastcore.xml import NotStr, to_xml
from fasthtml.common import A, Button, Div, Form, Input, Link, Script

from app.admin_ui import badge, card, empty_state, modal, page_header, two_column


def e(value):
    return escape(str(value if value is not None else ""), quote=True)


def assets():
    return (
        Link(rel="stylesheet", href="/static/admin/catalog.css"),
        Script(src="/static/admin/catalog.js", defer=True),
    )


def field(label, name, value="", kind="text", **attrs):
    attributes = " ".join(f'{e(k.replace("_", "-"))}="{e(v)}"' for k, v in attrs.items())
    return NotStr(
        f'<label>{e(label)}<input type="{e(kind)}" name="{e(name)}" '
        f'value="{e(value)}" {attributes}></label>'
    )


def area(label, name, value="", **attrs):
    return NotStr(
        f'<label {"hidden" if "hidden" in attrs else ""}>{e(label)}<textarea name="{e(name)}" '
        + " ".join(f'{e(k)}="{e(v)}"' for k, v in attrs.items())
        + f">{e(value)}</textarea></label>"
    )


def choice(label, name, values, selected=""):
    options = "".join(
        f'<option value="{e(v)}" {"selected" if str(v) == str(selected) else ""}>{e(t)}</option>'
        for v, t in values
    )
    return NotStr(
        f'<label>{e(label)}<select name="{e(name)}" aria-label="{e(label)}">{options}</select></label>'
    )


def check(label, name, checked=False):
    return NotStr(
        f'<label class="c-check"><input type="checkbox" name="{e(name)}" '
        f"{'checked' if checked else ''}>{e(label)}</label>"
    )


def hidden_json(name, data):
    return Input(type="hidden", name=name, value=json.dumps(data), data_catalog_json=name)


def product_index(site, products, total, counts, filters, page, csrf, collections):
    base = f"/admin/sites/{site.id}/products"
    rows = []
    for p, quantity in products:
        thumb = (
            f'<img src="{e(p.image_url)}" alt="" loading="lazy">'
            if p.image_url
            else '<span class="c-no-image">◇</span>'
        )
        rows.append(
            NotStr(
                f'<tr><td><input type="checkbox" name="product_ids" value="{e(p.id)}" aria-label="Select {e(p.name)}"></td>'
                f'<td><a class="c-product-link" href="{base}/{e(p.id)}">{thumb}{e(p.name)}</a></td>'
                f"<td>{to_xml(badge(p.status.title(), 'success' if p.status == 'active' else 'neutral'))}</td>"
                f"<td>{quantity} available</td><td>{e(p.product_type)}</td><td>{e(p.vendor)}</td></tr>"
            )
        )
    tabs = NotStr(
        '<nav class="a-tabs" aria-label="Product views">'
        + "".join(
            f'<a href="{base}?status={status}" class="{"is-active" if filters.get("status", "") == status else ""}">{label}</a>'
            for status, label in [
                ("", "All"),
                ("active", "Active"),
                ("draft", "Draft"),
                ("archived", "Archived"),
            ]
        )
        + "</nav>"
    )
    query_fields = [
        field(
            "Search products",
            "q",
            filters.get("q", ""),
            "search",
            placeholder="Search title, SKU or handle",
        ),
        choice(
            "Status",
            "status",
            [("", "All statuses")] + [(s, s.title()) for s in ("active", "draft", "archived")],
            filters.get("status"),
        ),
        field("Vendor", "vendor", filters.get("vendor", "")),
        field("Type", "product_type", filters.get("product_type", "")),
        field("Tag", "tag", filters.get("tag", "")),
        choice(
            "Collection",
            "collection",
            [("", "All collections")] + [(c.id, c.title) for c in collections],
            filters.get("collection"),
        ),
        choice(
            "Stock",
            "stock",
            [("", "All inventory"), ("low", "Low stock"), ("out", "Out of stock")],
            filters.get("stock"),
        ),
        choice(
            "Sort",
            "sort",
            [
                ("updated", "Recently updated"),
                ("title", "Title A to Z"),
                ("title_desc", "Title Z to A"),
                ("created", "Newest"),
            ],
            filters.get("sort"),
        ),
    ]
    from urllib.parse import urlencode

    def page_link(number):
        return base + "?" + urlencode({**filters, "page": number})

    labels = {"q": "Search", "status": "Status", "vendor": "Vendor", "product_type": "Type",
              "tag": "Tag", "collection": "Collection", "stock": "Stock"}
    collection_titles = {c.id: c.title for c in collections}
    chips = [A(f"{label}: {collection_titles.get(filters[key], filters[key]) if key == 'collection' else filters[key]} ×",
               href=base + "?" + urlencode({k: v for k, v in filters.items() if k != key}),
               cls="c-chip", aria_label=f"Remove {label.lower()} filter")
             for key, label in labels.items() if filters.get(key)]

    return Div(
        *assets(),
        page_header(
            "Products",
            breadcrumbs=[("Catalogue", base)],
            primary_action=A("Add product", href=base + "/new", cls="a-button a-button-primary"),
            secondary_actions=[
                A("Export all", href=base + "/export", cls="a-button"),
                A("Export view", href=base + "/export?" + urlencode(filters), cls="a-button"),
                A("Import", href=base + "/import", cls="a-button"),
            ],
        ),
        Div(
            *[
                Div(str(counts.get(key, 0)), NotStr(f"<span>{label}</span>"))
                for key, label in [
                    ("all", "Products"),
                    ("active", "Active"),
                    ("draft", "Draft"),
                    ("low", "Low stock"),
                ]
            ],
            cls="c-metrics",
        ),
        Div(
            tabs,
            Form(
                *query_fields,
                Button("Apply filters", cls="a-button"),
                A("Clear", href=base),
                method="get",
                cls="c-filters",
            ),
            Div(*chips, cls="c-filter-chips", aria_label="Active filters") if chips else None,
            Form(
                csrf,
                Div(
                    SpanCount(),
                    choice(
                        "Bulk action",
                        "action",
                        [
                            ("draft", "Set as draft"),
                            ("active", "Set as active"),
                            ("archive", "Archive"),
                            ("tags_add", "Add tags"),
                            ("tags_remove", "Remove tags"),
                            ("add_to_collection", "Add to collection"),
                            ("price_adjust", "Adjust price (minor units)"),
                            ("delete", "Delete"),
                        ],
                    ),
                    field("Tags / price adjustment", "value"),
                    choice(
                        "Collection to add",
                        "value_collection",
                        [("", "Choose collection")]
                        + [(c.id, c.title) for c in collections if c.kind == "manual"],
                    ),
                    Button("Apply", cls="a-button"),
                    cls="c-bulk",
                ),
                NotStr(
                    '<div class="a-table-wrap"><table><thead><tr><th><input type="checkbox" data-c-select-all aria-label="Select all products"></th><th>Product</th><th>Status</th><th>Inventory</th><th>Type</th><th>Vendor</th></tr></thead><tbody>'
                ),
                *rows,
                NotStr("</tbody></table></div>"),
                method="post",
                action=base + "/bulk",
                data_catalog_bulk="",
            ),
            empty_state(
                "No products match these filters" if counts.get("all") else "Add your first product",
                "Clear a filter or try another search." if counts.get("all") else "Create a product to start building your catalogue.",
                A("Clear filters", href=base, cls="a-button") if counts.get("all") else A("Add product", href=base + "/new", cls="a-button"),
            )
            if not total
            else None,
            Div(
                A("Previous", href=page_link(page - 1)) if page > 1 else None,
                f"Page {page} · {total} {'product' if total == 1 else 'products'}",
                A("Next", href=page_link(page + 1)) if page * 25 < total else None,
                cls="c-pagination",
            ),
            cls="a-card c-index",
        ),
    )


def SpanCount():
    return NotStr("<strong data-c-selection-count>0 selected</strong>")


def product_editor(
    site,
    product,
    variants,
    options,
    images,
    library,
    collections,
    selected_collections,
    warehouses,
    csrf,
    previous=None,
    following=None,
    currency="USD",
):
    base = f"/admin/sites/{site.id}/products"
    p = product

    def get(name, default=""):
        return getattr(p, name, default) if p else default

    toolbar = NotStr(
        '<div class="c-toolbar" role="toolbar" aria-label="Description formatting">'
        '<select data-rich-block aria-label="Paragraph style"><option value="p">Paragraph</option><option value="h2">Heading 2</option><option value="h3">Heading 3</option></select>'
        + "".join(
            f'<button type="button" data-rich-command="{command}" title="{label}">{label}</button>'
            for command, label in [
                ("bold", "Bold"),
                ("italic", "Italic"),
                ("underline", "Underline"),
                ("insertUnorderedList", "Bullets"),
                ("insertOrderedList", "Numbers"),
                ("createLink", "Link"),
                ("justifyLeft", "Left"),
                ("justifyCenter", "Centre"),
                ("justifyRight", "Right"),
                ("removeFormat", "Clear"),
            ]
        )
        + '<button type="button" data-rich-source>HTML</button></div>'
    )
    rich = Div(
        toolbar,
        NotStr(
            f'<div contenteditable="true" role="textbox" aria-label="Description" class="c-rich" data-rich-editor>{get("description_html")}</div>'
        ),
        area(
            "Description HTML",
            "description_html",
            get("description_html"),
            hidden="",
            **{"data-rich-text": ""},
        ),
        cls="c-rich-wrap",
    )
    image_data = [
        {"id": i.id, "image_url": i.image_url, "alt": i.alt, "media_id": i.media_id} for i in images
    ]
    media = card(
        NotStr(
            '<div class="c-drop" data-media-drop><input type="file" name="images_upload" accept="image/jpeg,image/png,image/webp" multiple aria-label="Upload product images"><p>Drop images here or choose files</p><small>JPEG, PNG or WebP, up to 8 MB each</small></div>'
        ),
        hidden_json("images_json", image_data),
        Div(data_media_list="", cls="c-media-grid"),
        choice(
            "Select existing image",
            "media_picker",
            [("", "Choose from media library")]
            + [(m.id, m.title or m.alt or "Image") for m in library],
        ),
        NotStr('<button type="button" class="a-button" data-media-add>Add selected image</button>'),
        hidden_json(
            "media_library_json",
            [
                {
                    "media_id": m.id,
                    "image_url": m.public_url or f"/site-media/{site.id}/{m.id}",
                    "alt": m.alt,
                }
                for m in library
            ],
        ),
        title="Media",
    )
    first = variants[0] if variants else {}
    price = first.get("price_minor", 0) or 0
    cost = first.get("cost_minor", 0) or 0
    pricing = card(
        NotStr(f'<p class="c-muted">Prices in {e(currency)}</p>'),
        Div(
            field(
                "Price",
                "price",
                f"{price / 100:.2f}",
                "number",
                step="0.01",
                min="0",
                data_profit_price="",
            ),
            field(
                "Compare-at price",
                "compare_at_price",
                f"{(first.get('compare_at_minor') or 0) / 100:.2f}",
                "number",
                step="0.01",
                min="0",
            ),
            field(
                "Cost per item",
                "cost",
                f"{cost / 100:.2f}",
                "number",
                step="0.01",
                min="0",
                data_profit_cost="",
            ),
            cls="c-grid",
        ),
        NotStr("<p data-profit-output></p>"),
        check("Charge tax on this product", "charge_tax", get("charge_tax", True)),
        field("Tax code", "tax_code", get("tax_code")),
        title="Pricing",
    )
    inventory = card(
        check("Track quantity", "track_inventory", first.get("track_inventory", True)),
        Div(
            field("SKU", "sku", first.get("sku", "")),
            field("Barcode", "barcode", first.get("barcode", "")),
            cls="c-grid",
        ),
        *[
            field(
                f"{w.name} quantity",
                f"quantity_{w.id}",
                first.get("quantities", {}).get(w.id, 0),
                "number",
                min="0",
            )
            for w in warehouses
        ],
        check(
            "Continue selling when out of stock",
            "continue_selling_when_out_of_stock",
            first.get("continue_selling_when_out_of_stock", False),
        ),
        title="Inventory",
    )
    shipping = card(
        check("This is a physical product", "requires_shipping", get("requires_shipping", True)),
        Div(
            field("Weight (g)", "weight_grams", get("weight_grams", 0), "number", min="0"),
            field("Country of origin (ISO code)", "country_of_origin", get("country_of_origin")),
            field("HS code", "hs_code", get("hs_code")),
            cls="c-grid",
        ),
        Div(
            *[
                field(
                    f"{name.title()} (mm)",
                    f"dimension_{name}",
                    (get("dimensions_json", {}) or {}).get(name, ""),
                    "number",
                    min="0",
                )
                for name in ("length", "width", "height")
            ],
            cls="c-grid",
        ),
        title="Shipping",
    )
    variant_card = card(
        hidden_json("options_json", options),
        hidden_json("variants_json", variants),
        Div(data_options_list=""),
        NotStr(
            '<button type="button" class="a-button" data-option-add>Add options like size or colour</button>'
        ),
        NotStr(
            '<p class="c-muted">Up to 3 options and 100 variants. Existing prices, SKUs and stock are preserved when values remain.</p><div data-variant-bulk><label>Selected variant price<input type="number" step="0.01" data-variant-bulk-price></label><button type="button" class="a-button" data-variant-bulk-apply>Apply to selected</button></div><div class="a-table-wrap" data-variants-table></div>'
        ),
        title="Variants",
    )
    seo = card(
        NotStr(
            f'<div class="c-seo"><strong data-seo-preview-title>{e(get("seo_title") or get("name"))}</strong><span>/products/<span data-seo-preview-handle>{e(get("slug"))}</span></span><p data-seo-preview-description>{e(get("seo_description"))}</p></div>'
        ),
        field("SEO title", "seo_title", get("seo_title"), maxlength="70"),
        area("SEO description", "seo_description", get("seo_description"), maxlength="320"),
        field("URL handle", "slug", get("slug"), pattern=r"[a-z0-9\-]+"),
        title="Search engine listing",
    )
    main = Div(
        card(
            field("Title", "name", get("name"), required="", maxlength="220"),
            rich,
            area("Short description", "short_description", get("short_description")),
        ),
        media,
        pricing,
        inventory,
        shipping,
        variant_card,
        seo,
        cls="c-stack",
    )
    aside = Div(
        card(
            choice(
                "Status",
                "status",
                [(s, s.title()) for s in ("active", "draft", "archived")],
                get("status", "draft"),
            ),
            title="Status",
        ),
        card(
            check("Online store", "published", get("is_published", False)),
            NotStr(f'<p class="c-muted">{e(site.name)}</p>'),
            title="Publishing",
        ),
        card(
            field("Product type", "product_type", get("product_type")),
            field("Vendor", "vendor", get("vendor")),
            field(
                "Tags (comma separated)",
                "tags",
                ", ".join(get("tags_json", []) or []),
                data_tags_input="",
            ),
            Div(data_tags_chips="", cls="c-chips"),
            NotStr(
                '<label>Collections<select name="collection_ids" multiple>'
                + "".join(
                    f'<option value="{e(c.id)}" {"selected" if c.id in selected_collections else ""}>{e(c.title)}</option>'
                    for c in collections
                    if c.kind == "manual"
                )
                + "</select></label>"
            ),
            title="Product organisation",
        ),
        card(
            field("Theme template", "template", get("template", "default")), title="Theme template"
        ),
        cls="c-stack",
    )
    actions = [
        A("Previous", href=f"{base}/{previous}", cls="a-button") if previous else None,
        A("Next", href=f"{base}/{following}", cls="a-button") if following else None,
    ]
    more = (
        NotStr(
            '<details class="c-more"><summary class="a-button">More actions</summary>'
            + "".join(
                f'<button type="submit" form="product-actions" name="action" value="{action}">{label}</button>'
                for action, label in [
                    ("duplicate", "Duplicate"),
                    ("archive", "Archive"),
                    ("delete", "Delete"),
                ]
            )
            + "</details>"
        )
        if p
        else None
    )
    return Div(
        *assets(),
        page_header(
            get("name") or "Add product",
            back_href=base,
            breadcrumbs=[("Products", base)],
            secondary_actions=[*actions, more],
            primary_action=Button(
                "Save", form="catalog-product-form", cls="a-button a-button-primary"
            ),
        ),
        Form(
            csrf,
            Input(type="hidden", name="version", value=get("version", 1)),
            two_column(main, aside),
            Button("Save product", cls="a-button a-button-primary"),
            id="catalog-product-form",
            data_dirty_guard="",
            data_catalog_editor="",
            method="post",
            enctype="multipart/form-data",
            action=f"{base}/{p.id if p else 'new'}",
        ),
        Form(
            Input(type="hidden", name="csrf_token", value=csrf.attrs.get("value", "")),
            Input(type="hidden", name="version", value=get("version", 1)),
            method="post",
            action=f"{base}/{p.id}/actions",
            id="product-actions",
        )
        if p
        else None,
        cls="c-catalog",
    )


def import_preview(result):
    if not isinstance(result, dict):
        result = vars(result)
    products = result.get("products", [])
    errors = result.get("errors", [])
    summary = NotStr(
        f"<p><strong>{e(result.get('rows', 0))} rows checked</strong> · "
        f"{e(result.get('created', 0))} {'product' if result.get('created') == 1 else 'products'} to create · "
        f"{e(result.get('updated', 0))} {'product' if result.get('updated') == 1 else 'products'} to update · "
        f"{len(errors)} errors</p>"
    )
    table = NotStr(
        '<div class="a-table-wrap"><table><thead><tr><th>Product</th><th>Handle</th><th>Variants</th></tr></thead><tbody>'
        + "".join(
            f"<tr><td>{e(p.get('title'))}</td><td>{e(p.get('handle'))}</td><td>{e(p.get('variants'))}</td></tr>"
            for p in products
        )
        + "</tbody></table></div>"
    )
    error_table = (
        NotStr(
            '<div role="alert"><h3>Rows requiring attention</h3><table><thead><tr><th>Row</th><th>Handle</th><th>Error</th></tr></thead><tbody>'
            + "".join(
                f"<tr><td>{e(r.get('row'))}</td><td>{e(r.get('handle', ''))}</td><td>{e(r.get('error'))}</td></tr>"
                for r in errors
            )
            + "</tbody></table></div>"
        )
        if errors
        else None
    )
    return card(summary, table, error_table, title="Import results")


def collection_index(site, rows):
    base = f"/admin/sites/{site.id}/collections"
    return Div(
        *assets(),
        page_header(
            "Collections",
            primary_action=A(
                "Create collection", href=base + "/new", cls="a-button a-button-primary"
            ),
        ),
        card(
            NotStr(
                '<div class="a-table-wrap"><table><thead><tr><th>Title</th><th>Products</th><th>Type</th><th>Publishing</th></tr></thead><tbody>'
                + "".join(
                    f'<tr><td><a href="{base}/{e(c.id)}">{e(c.title)}</a></td><td>{count}</td><td>{e(c.kind.title())}</td><td>{"Published" if c.is_published else "Draft"}</td></tr>'
                    for c, count in rows
                )
                + "</tbody></table></div>"
            )
        )
        if rows
        else empty_state(
            "Group your products",
            "Create manual collections or let rules organise products automatically.",
            A("Create collection", href=base + "/new", cls="a-button"),
        ),
    )


def collection_editor(site, collection, products, selected, csrf, count=0):
    c = collection

    def get(key, default=""):
        return getattr(c, key, default) if c else default

    base = f"/admin/sites/{site.id}/collections"
    rules = get("rules_json", {}) or {"match": "all", "rules": []}
    options = "".join(f'<option value="{e(p.id)}">{e(p.name)}</option>' for p in products)
    picker_data = [{"id": p.id, "title": p.name} for p in products]
    manual = Div(
        field("Search products", "product_search", kind="search", data_collection_search=""),
        NotStr(
            f'<label>Choose product<select data-collection-picker aria-label="Choose product">{options}</select></label><button type="button" class="a-button" data-collection-add>Add product</button>'
        ),
        hidden_json("product_ids_json", selected),
        hidden_json("product_picker_json", picker_data),
        Div(data_collection_list="", cls="c-collection-list"),
        data_collection_manual="",
    )
    automated = Div(
        choice(
            "Products must match",
            "match",
            [("all", "All conditions"), ("any", "Any condition")],
            rules.get("match", "all"),
        ),
        hidden_json("rules_json", rules),
        Div(data_rules_list=""),
        NotStr('<button type="button" class="a-button" data-rule-add>Add condition</button>'),
        NotStr(
            f'<p role="status" data-rule-preview>{count} matching {"product" if count == 1 else "products"}</p><button type="button" class="a-button" data-rule-preview-button>Preview matches</button>'
        ),
        data_collection_automated="",
    )
    main = Div(
        card(
            field("Title", "title", get("title"), required=""),
            area("Description", "description", get("description")),
        ),
        card(
            choice(
                "Collection type",
                "kind",
                [("manual", "Manual"), ("automated", "Automated")],
                get("kind", "manual"),
            ),
            manual,
            automated,
            title="Products",
        ),
        card(
            field("SEO title", "seo_title", get("seo_title")),
            area("SEO description", "seo_description", get("seo_description")),
            field("URL handle", "handle", get("handle")),
            title="Search engine listing",
        ),
        cls="c-stack",
    )
    aside = Div(
        card(
            check("Published on online store", "is_published", get("is_published", False)),
            title="Publishing",
        ),
        card(field("Collection image URL", "image_url", get("image_url")), title="Image"),
        card(
            choice(
                "Sort order",
                "sort_order",
                [
                    ("manual", "Manual"),
                    ("title", "Title A to Z"),
                    ("price_asc", "Price low to high"),
                    ("price_desc", "Price high to low"),
                    ("newest", "Newest"),
                ],
                get("sort_order", "manual"),
            ),
            title="Sorting",
        ),
        cls="c-stack",
    )
    return Div(
        *assets(),
        page_header(
            get("title") or "Create collection",
            back_href=base,
            breadcrumbs=[("Collections", base)],
            primary_action=Button(
                "Save collection", form="collection-form", cls="a-button a-button-primary"
            ),
        ),
        Form(
            csrf,
            Input(type="hidden", name="version", value=get("version", 1)),
            two_column(main, aside),
            Button("Save collection", cls="a-button a-button-primary"),
            id="collection-form",
            method="post",
            data_dirty_guard="",
            data_collection_editor="",
            data_preview_url=base + "/preview",
        ),
    )


def inventory_page(site, rows, warehouses, history, csrf, threshold, q="", low_stock=False):
    base = f"/admin/sites/{site.id}/inventory"
    rendered = []
    for product, variant, quantities in rows:
        adjustment = Form(
            csrf,
            Input(type="hidden", name="variant_id", value=variant.id),
            choice("Location", "warehouse_id", [(w.id, w.name) for w in warehouses]),
            choice("Mode", "mode", [("adjust", "Adjust by"), ("set", "Set to")]),
            field("Quantity", "quantity", "0", "number", step="1"),
            choice(
                "Reason",
                "reason",
                [(r, r.title()) for r in ("correction", "received", "damaged", "returned", "sale")],
            ),
            field("Note", "note"),
            Button("Save adjustment", cls="a-button"),
            method="post",
            cls="c-adjust",
        )
        summary = "".join(
            f"<span>{e(w.name)}: {quantities.get(w.id, (0, 0))[0] - quantities.get(w.id, (0, 0))[1]} available · {quantities.get(w.id, (0, 0))[1]} committed</span>"
            for w in warehouses
        )
        rendered.append(
            Div(
                Div(
                    A(product.name, href=f"/admin/sites/{site.id}/products/{product.id}"),
                    NotStr(f"<small>{e(variant.name)} · {e(variant.sku)}</small>"),
                    cls="c-inventory-name",
                    role="cell",
                ),
                NotStr('<div class="c-location-quantities" role="cell">' + summary + "</div>"),
                NotStr('<div role="cell"><details><summary class="a-button">Adjust inventory</summary>'),
                adjustment,
                NotStr("</details></div>"),
                cls="c-inventory-row",
                role="row",
            )
        )
    history_drawer = modal("Adjustment history", NotStr(
        '<div class="a-table-wrap"><table><thead><tr><th>Date</th><th>Variant</th><th>Change</th><th>Result</th><th>Reason</th><th>Note</th></tr></thead><tbody>'
        + "".join(
            f"<tr><td>{e(h.created_at.strftime('%d %b %Y %H:%M'))}</td><td>{e(name)}</td><td>{h.delta:+d}</td><td>{h.resulting_quantity}</td><td>{e(h.reason)}</td><td>{e(h.note)}</td></tr>"
            for h, name in history)
        + "</tbody></table></div>"), id="inventory-history")
    history_drawer.attrs["class"] += " c-history-drawer"
    return Div(
        *assets(),
        page_header("Inventory", breadcrumbs=[("Products", f"/admin/sites/{site.id}/products")],
                    secondary_actions=[Button("Adjustment history", type="button", cls="a-button",
                                              data_modal_open="inventory-history")]),
        card(
            Form(
                field("Search inventory", "q", q, "search"),
                check("Low stock only", "low_stock", low_stock),
                Button("Filter", cls="a-button"),
                method="get",
                cls="c-filters",
            ),
            Form(
                csrf,
                field("Low-stock threshold", "threshold", threshold, "number", min="0"),
                Button("Save threshold", cls="a-button"),
                method="post",
                action=base + "/settings",
                cls="c-threshold",
            ),
        ),
        card(Div(
            NotStr('<div class="c-inventory-head" role="row"><strong role="columnheader">Product / SKU</strong><strong role="columnheader">Locations</strong><strong role="columnheader">Actions</strong></div>'),
            *rendered, role="table", aria_label="Variant inventory"))
        if rendered
        else empty_state(
            "No matching inventory", "Add products or change your filters to manage inventory."
        ),
        history_drawer,
    )
