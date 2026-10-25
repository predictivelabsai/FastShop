"""Shared safe widget definitions and rendering for both storefront themes."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from fasthtml.common import (
    A,
    Blockquote,
    Button,
    Details,
    Div,
    Form,
    Hr,
    Iframe,
    Img,
    Input,
    Label,
    Li,
    NotStr,
    P,
    Section,
    Small,
    Span,
    Summary,
    Ul,
    Video,
    ft_hx,
)
from sqlalchemy import select

from app.services import CommerceError

WIDGETS = {
    "container": (
        "Container",
        "Layout",
        {
            "columns": 2,
            "gap": 24,
            "vertical_alignment": "start",
            "stack_mobile": True,
            "children": [],
        },
    ),
    "heading": ("Heading", "Text", {"heading": "Your heading", "level": "h2", "alignment": "left"}),
    "richtext": ("Rich text", "Text", {"body": "<p>Tell your story here.</p>"}),
    "image": (
        "Image",
        "Media",
        {"image": "", "alt": "Describe your image", "link": "", "aspect": "auto", "rounded": 0},
    ),
    "button": ("Button", "Text", {"button": "Explore", "url": "/", "button_style": "primary"}),
    "spacer": ("Spacer", "Layout", {"height": 48}),
    "divider": ("Divider", "Layout", {"thickness": 1}),
    "video": ("Video", "Media", {"video": "", "poster": "", "alt": "Video"}),
    "icon_list": (
        "Icon list",
        "Text",
        {"items": [{"heading": "Your feature", "body": "Add a useful detail.", "theme": "check"}]},
    ),
    "image_gallery": (
        "Image gallery",
        "Media",
        {"columns": 3, "gap": 16, "items": [{"image": "", "alt": "Gallery image", "url": ""}]},
    ),
    "testimonial": (
        "Testimonial",
        "Social proof",
        {"heading": "Customer name", "body": "Share a customer story.", "rating": 5},
    ),
    "product_card": ("Product card", "Commerce", {"product": ""}),
    "product_grid": (
        "Product grid",
        "Commerce",
        {"products": [], "collection": "", "columns": 3, "limit": 6},
    ),
    "collection_list": (
        "Collection list", "Commerce", {"collections": [], "columns": 3, "limit": 6},
    ),
    "newsletter": (
        "Newsletter signup",
        "Forms",
        {"heading": "Stay in the loop", "body": "Hear about our latest updates."},
    ),
    "accordion": (
        "FAQ / accordion",
        "Text",
        {"items": [{"heading": "Your question", "body": "Your answer."}]},
    ),
    "html": ("HTML / secure embed", "Advanced", {"body": "<p>Add your content.</p>", "url": ""}),
}
WIDGET_FIELDS = set().union(*(set(v[2]) for v in WIDGETS.values())) - {"children", "items"}
STYLE_FIELDS = {
    "padding_top",
    "padding_bottom",
    "margin_top",
    "margin_bottom",
    "background_colour",
    "background_image",
    "text_colour",
    "alignment",
    "max_width",
    "hide_desktop",
    "hide_tablet",
    "hide_mobile",
    "anchor_id",
    "css_class",
}


class _RichText(HTMLParser):
    tags = {
        "p",
        "br",
        "strong",
        "em",
        "u",
        "s",
        "a",
        "ul",
        "ol",
        "li",
        "h2",
        "h3",
        "h4",
        "blockquote",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "object", "svg", "math"}:
            self.suppressed += 1
        if self.suppressed or tag not in self.tags:
            return
        attributes = ""
        if tag == "a":
            from app.content import safe_url

            href = dict(attrs).get("href", "")
            try:
                href = safe_url(href)
            except CommerceError:
                href = ""
            attributes = ' href="' + html.escape(href, quote=True) + '" rel="noopener noreferrer"'
        self.output.append("<" + tag + attributes + ">")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "object", "svg", "math"}:
            self.suppressed = max(0, self.suppressed - 1)
            return
        if not self.suppressed and tag in self.tags and tag != "br":
            self.output.append("</" + tag + ">")

    def handle_data(self, data):
        if not self.suppressed:
            self.output.append(html.escape(data))


def sanitise_html(value):
    parser = _RichText()
    parser.feed(value)
    return "".join(parser.output)


def validate_style(style):
    from app.content import safe_url

    if not isinstance(style, dict) or set(style) - STYLE_FIELDS:
        raise CommerceError("Choose supported style settings.")
    for key, value in style.items():
        if key in {"padding_top", "padding_bottom", "margin_top", "margin_bottom", "max_width"}:
            if type(value) is not int or not 0 <= value <= (2400 if key == "max_width" else 300):
                raise CommerceError("Spacing and width must be within the allowed range.")
        elif key.startswith("hide_"):
            if type(value) is not bool:
                raise CommerceError("Device visibility must be true or false.")
        elif key.endswith("_colour"):
            if not isinstance(value, str) or not re.fullmatch(
                r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", value
            ):
                raise CommerceError("Use a hexadecimal colour.")
        elif key == "background_image":
            if not isinstance(value, str):
                raise CommerceError("Choose a valid background image.")
            if not re.fullmatch(r"metafield:[a-z][a-z0-9_]{0,79}\.[a-z][a-z0-9_]{0,79}", value):
                safe_url(value, media=True)
        elif key == "alignment":
            if not isinstance(value, str) or value not in {"left", "centre", "center", "right"}:
                raise CommerceError("Choose an alignment.")
        elif not isinstance(value, str) or (
            value and not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value)
        ):
            raise CommerceError("Use a safe lowercase CSS slug.")
    return style


def validate_widget(block):
    kind = block["type"]
    defaults = WIDGETS[kind][2]
    for key, default in defaults.items():
        if key not in block or key in {"children", "items"}:
            continue
        value = block[key]
        if type(default) is bool and type(value) is not bool:
            raise CommerceError(f"{key} must be true or false.")
        if type(default) is int:
            maximum = {
                "columns": 4,
                "gap": 120,
                "height": 600,
                "rounded": 100,
                "thickness": 20,
                "rating": 5,
                "limit": 24,
            }[key]
            minimum = 1 if key in {"columns", "thickness", "rating", "limit"} else 0
            if type(value) is not int or not minimum <= value <= maximum:
                raise CommerceError(f"{key} must be between {minimum} and {maximum}.")
        if isinstance(default, str) and key not in {
            "heading",
            "body",
            "image",
            "alt",
            "link",
            "url",
            "button",
            "video",
            "poster",
        }:
            if not isinstance(value, str) or len(value) > 128:
                raise CommerceError(f"{key} must be a short string.")
    choices = {
        "level": {"h1", "h2", "h3", "h4", "h5", "h6"},
        "alignment": {"left", "centre", "center", "right"},
        "aspect": {"auto", "1/1", "4/3", "16/9", "3/4"},
        "button_style": {"primary", "secondary", "link"},
        "vertical_alignment": {"start", "center", "end", "stretch"},
    }
    for key, options in choices.items():
        if key in block and (not isinstance(block[key], str) or block[key] not in options):
            raise CommerceError(f"Choose a supported {key}.")
    for key in {"product", "collection"} & block.keys():
        if block[key] and not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", block[key]):
            raise CommerceError("Choose a valid product or collection.")
    if kind == "icon_list":
        for item in block.get("items", []):
            if not isinstance(item.get("theme", "check"), str) or item.get(
                "theme", "check"
            ) not in {"check", "star", "arrow", "heart"}:
                raise CommerceError("Choose a supported list icon.")
    if kind in {"richtext", "html"} and "body" in block:
        body = block["body"]
        block["body"] = (
            {locale: sanitise_html(v) for locale, v in body.items()}
            if isinstance(body, dict)
            else sanitise_html(body)
        )
    for key in {"products", "collections"} & block.keys():
        if (not isinstance(block[key], list) or len(block[key]) > 24 or any(
            not isinstance(v, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", v)
            for v in block[key]
        )):
            raise CommerceError(f"Choose up to 24 valid {key}.")
    if kind in {"video", "html"}:
        from app.content import safe_url

        value = block.get("video" if kind == "video" else "url", "")
        for v in value.values() if isinstance(value, dict) else [value]:
            if v and not (
                safe_url(v).startswith("https://") or kind == "video" and v.startswith("/")
            ):
                raise CommerceError("Embedded media must use HTTPS.")
    return block


WIDGET_CSS = """.fs-widget{box-sizing:border-box;max-width:var(--store-width,1200px);margin-inline:auto;padding:24px;position:relative}.fs-widget img{max-width:100%;height:auto;display:block}.fs-widget video,.fs-widget iframe{width:100%;aspect-ratio:16/9;border:0}.fs-widget-grid{display:grid;grid-template-columns:repeat(var(--fs-columns,2),minmax(0,1fr));gap:var(--fs-gap,24px)}.fs-widget .fs-widget{width:100%;padding:16px}.fs-widget-button{display:inline-block;padding:12px 24px;border-radius:var(--store-radius,var(--button-radius,6px));background:var(--store-accent,var(--primary,var(--accent,#145cce)));color:white;text-decoration:none}.fs-widget-secondary{background:transparent;border:1px solid currentColor;color:inherit}.fs-widget-link{background:transparent;color:inherit;padding:0;text-decoration:underline}.fs-widget details{border-bottom:1px solid #ddd;padding:16px 0}.fs-widget summary{cursor:pointer;font-weight:600}.fs-widget-placeholder{min-height:140px;background:#f2f3f4;display:grid;place-items:center;color:#656b73}.fs-widget blockquote{margin:0;font-size:1.2em}.fs-widget ul{padding-left:24px}@media(min-width:1025px){.fs-hide-desktop{display:none!important}}@media(min-width:641px) and (max-width:1024px){.fs-hide-tablet{display:none!important}}@media(max-width:640px){.fs-hide-mobile{display:none!important}.fs-stack-mobile{grid-template-columns:1fr!important}}"""


def strip_editor_hooks(node):
    """Keep classic previews byte-compatible unless the visual editor requested hooks."""
    if isinstance(node, (list, tuple)):
        for child in node:
            strip_editor_hooks(child)
    elif hasattr(node, "attrs"):
        for key in ("data-block-id", "data-block-type", "data-field", "data-field-type"):
            node.attrs.pop(key, None)
        for child in getattr(node, "children", []):
            strip_editor_hooks(child)
    return node


def needs_widget_css(blocks):
    return any(block["type"] in WIDGETS or block.get("style") or
               needs_widget_css(block.get("children", [])) for block in blocks)


def decorate_block(node, block, preview=False):
    """Apply validated merchant styles without changing legacy nodes without styles."""
    if node is None:
        return node
    if isinstance(node, (list, tuple)):
        return [decorate_block(n, block, preview) for n in node]
    if not hasattr(node, "attrs"):
        return node
    style = block.get("style", {})
    rules = []
    mapping = {
        "padding_top": "padding-top",
        "padding_bottom": "padding-bottom",
        "margin_top": "margin-top",
        "margin_bottom": "margin-bottom",
        "max_width": "max-width",
        "background_colour": "background-color",
        "text_colour": "color",
        "alignment": "text-align",
    }
    for key, prop in mapping.items():
        if key in style:
            value = str(style[key]) + ("px" if type(style[key]) is int else "")
            rules.append(f"{prop}:{value.replace('centre', 'center')}")
    if style.get("background_image"):
        from urllib.parse import quote

        rules.append(
            'background-image:url("' + quote(style["background_image"], safe="/:?=&%") + '")'
        )
        rules.append("background-size:cover")
    if rules:
        node.attrs["style"] = node.attrs.get("style", "") + ";" + ";".join(rules)
    classes = [style.get("css_class", "")] + [
        "fs-hide-" + device
        for device in ("desktop", "tablet", "mobile")
        if style.get("hide_" + device)
    ]
    if any(classes):
        node.attrs["class"] = node.attrs.get("class", "") + " " + " ".join(classes)
    if style.get("anchor_id"):
        node.attrs["id"] = style["anchor_id"]
    if preview:
        node.attrs.update({"data-block-id": block["id"], "data-block-type": block["type"]})
        _mark_fields(node, block)
    return node


def _node_text(node):
    if isinstance(node, str):
        return node
    return "".join(_node_text(child) for child in getattr(node, "children", []))


def _mark_fields(node, block):
    for child in getattr(node, "children", []):
        if hasattr(child, "attrs") and child.attrs.get("data-block-id"):
            continue
        tag = getattr(child, "tag", "")
        candidates = (
            ["heading"]
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}
            else ["body", "eyebrow"]
            if tag in {"p", "small"}
            else ["button"]
            if tag in {"a", "button"}
            else []
        )
        for field in candidates:
            value = block.get(field)
            if isinstance(value, str) and value and _node_text(child).strip() == value.strip():
                child.attrs["data-field"] = field
                child.attrs["data-field-type"] = "text"
                break
        if tag == "img" and child.attrs.get("src") == block.get("image"):
            child.attrs["data-field"] = "image"
        _mark_fields(child, block)


def render_widget(db, site, page, config, block, base, csrf, preview, render_child):
    kind = block["type"]
    if kind not in WIDGETS:
        return None

    def field(name):
        return (
            {
                "data_field": name,
                "data_field_type": "richtext"
                if name == "body" and kind in {"richtext", "html"}
                else "text",
            }
            if preview
            else {}
        )

    children = []
    css = ""
    if kind == "container":
        children = [
            render_child(child) for child in block.get("children", []) if not child.get("hidden")
        ]
        css = f"--fs-columns:{block.get('columns', 2)};--fs-gap:{block.get('gap', 24)}px;align-items:{block.get('vertical_alignment', 'start')}"
    elif kind == "heading":
        children = [
            ft_hx(
                block.get("level", "h2"),
                block.get("heading", ""),
                style="text-align:" + block.get("alignment", "left").replace("centre", "center"),
                **field("heading"),
            )
        ]
    elif kind in {"richtext", "html"}:
        children = [Div(NotStr(sanitise_html(block.get("body", ""))), **field("body"))]
        if kind == "html" and block.get("url"):
            children.append(
                Iframe(
                    src=block["url"],
                    sandbox="",
                    loading="lazy",
                    referrerpolicy="no-referrer",
                    title="Embedded content",
                )
            )
    elif kind == "image":
        media = (
            Img(
                src=block["image"],
                alt=block.get("alt", ""),
                style=f"border-radius:{block.get('rounded', 0)}px;aspect-ratio:{block.get('aspect', 'auto')};object-fit:cover",
                **field("image"),
            )
            if block.get("image")
            else Div("Choose an image", cls="fs-widget-placeholder", **field("image"))
        )
        children = [
            A(media, href=base + block["link"] if block["link"].startswith("/") else block["link"])
            if block.get("link")
            else media
        ]
    elif kind == "button":
        children = [
            A(
                block.get("button", "Explore"),
                href=base + block.get("url", "/")
                if block.get("url", "/").startswith("/")
                else block["url"],
                cls="fs-widget-button fs-widget-" + block.get("button_style", "primary"),
                **field("button"),
            )
        ]
    elif kind == "spacer":
        css = f"height:{block.get('height', 48)}px;padding:0"
    elif kind == "divider":
        children = [
            Hr(style=f"border:0;border-top:{block.get('thickness', 1)}px solid currentColor")
        ]
    elif kind == "video":
        value = block.get("video", "")
        children = [
            Video(src=value, controls=True, poster=block.get("poster", ""), preload="none")
            if value.lower().split("?")[0].endswith((".mp4", ".webm", ".ogg"))
            else Iframe(
                src=value,
                sandbox="allow-scripts allow-presentation",
                referrerpolicy="no-referrer",
                title=block.get("alt", "Video"),
                loading="lazy",
            )
            if value
            else Div("Choose a video", cls="fs-widget-placeholder")
        ]
    elif kind == "icon_list":
        children = [
            Ul(
                *[
                    Li(
                        Span(
                            {"check": "✓ ", "star": "★ ", "arrow": "→ ", "heart": "♥ "}[
                                item.get("theme", "check")
                            ],
                            aria_hidden="true",
                        ),
                        Span(item.get("heading", "")),
                        P(item.get("body", "")),
                    )
                    for item in block.get("items", [])
                ]
            )
        ]
    elif kind == "image_gallery":
        for item in block.get("items", []):
            media = (
                Img(src=item["image"], alt=item.get("alt", ""), loading="lazy")
                if item.get("image")
                else Div("Choose an image", cls="fs-widget-placeholder")
            )
            children.append(
                A(media, href=base + item["url"] if item["url"].startswith("/") else item["url"])
                if item.get("url")
                else media
            )
        css = f"--fs-columns:{block.get('columns', 3)};--fs-gap:{block.get('gap', 16)}px"
    elif kind == "testimonial":
        children = [
            Span("★" * block.get("rating", 5), aria_label=f"{block.get('rating', 5)} out of 5"),
            Blockquote(block.get("body", ""), **field("body")),
            P(block.get("heading", ""), **field("heading")),
        ]
    elif kind in {"product_card", "product_grid"}:
        from app.models import Product

        query = select(Product).where(
            Product.tenant_id == site.tenant_id, Product.is_published.is_(True)
        )
        identifiers = (
            [block.get("product")] if kind == "product_card" else block.get("products", [])
        )
        if identifiers:
            query = query.where(Product.id.in_(identifiers))
        elif kind == "product_card":
            query = query.where(Product.id == "")
        from app.content import site_pages

        paths = {p.path for p in site_pages(db, site) if p.published_json}
        query = query.where(("/products/" + Product.slug).in_(paths))
        limit = block.get("limit", 1 if kind == "product_card" else 6)
        if kind == "product_grid" and block.get("collection"):
            from app.catalog_admin import collection_products
            from app.models import Category, Collection

            collection = db.scalar(select(Collection).where(
                Collection.tenant_id == site.tenant_id, Collection.id == block["collection"],
                Collection.is_published.is_(True),
            ))
            if collection:
                selected = set(db.scalars(query.with_only_columns(Product.id)))
                products = [p for p in collection_products(db, site, collection) if p.id in selected]
            else:
                # Stored category IDs predate P2 and must keep rendering existing pages.
                products = list(db.scalars(query.join(Category).where(
                    Category.tenant_id == site.tenant_id, Category.id == block["collection"],
                ).limit(limit)))
        else:
            products = list(db.scalars(query.limit(limit)))
        children = [
            A(
                Img(src=p.image_url, alt=p.name, loading="lazy") if p.image_url else None,
                P(p.name), href=base + "/products/" + p.slug,
            )
            for p in products[:limit]
        ]
        children = children or [Div("Choose products to display", cls="fs-widget-placeholder")]
        css = f"--fs-columns:{block.get('columns', 3)}"
    elif kind == "collection_list":
        from app.content import site_pages
        from app.models import Collection

        paths = {p.collection_id: p.path for p in site_pages(db, site)
                 if p.published_json and p.collection_id}
        query = select(Collection).where(Collection.tenant_id == site.tenant_id,
                                         Collection.is_published.is_(True))
        identifiers = block.get("collections", [])
        if identifiers:
            query = query.where(Collection.id.in_(identifiers))
        collections = list(db.scalars(query.order_by(Collection.title)))
        if identifiers:
            collections.sort(key=lambda c: identifiers.index(c.id))
        children = [A(
            Img(src=c.image_url, alt=c.title, loading="lazy") if c.image_url else None,
            P(c.title), href=base + paths[c.id],
        ) for c in [c for c in collections if c.id in paths][:block.get("limit", 6)]]
        children = children or [Div("Choose collections to display", cls="fs-widget-placeholder")]
        css = f"--fs-columns:{block.get('columns', 3)}"
    elif kind == "newsletter":
        from app.commerce import payments_enabled, settings_for

        commerce_config = settings_for(db, site)
        newsletter_enabled = bool(
            commerce_config
            and payments_enabled(db, site, commerce_config)
            and site.status in {"preview", "published"}
            and not preview
        )
        children = [
            ft_hx("h2", block.get("heading", ""), **field("heading")),
            P(block.get("body", ""), **field("body")),
            Form(
                Input(type="hidden", name="csrf_token", value=csrf),
                Label(
                    "Email address",
                    Input(
                        type="email",
                        name="email",
                        required=True,
                        maxlength=320,
                        autocomplete="email",
                        disabled=not newsletter_enabled,
                    ),
                ),
                Label(
                    Input(
                        type="checkbox",
                        name="marketing_consent",
                        required=True,
                        disabled=not newsletter_enabled,
                    ),
                    " I agree to receive marketing emails. ",
                    A("Privacy policy", href=base + "/pages/privacy-policy"),
                ),
                Input(name="website", tabindex="-1", autocomplete="off", hidden=True),
                Button(
                    "Subscribe",
                    type="submit",
                    disabled=not newsletter_enabled,
                    cls="fs-widget-button",
                ),
                method="post",
                action=base + "/newsletter",
            ),
            Small("Preview only. Email signup is not available; no email is collected.")
            if not newsletter_enabled
            else None,
        ]
    elif kind == "accordion":
        children = [
            Details(Summary(i.get("heading", "")), P(i.get("body", "")))
            for i in block.get("items", [])
        ]
    classes = "fs-widget fs-widget-" + kind
    if kind in {"container", "image_gallery", "product_grid", "collection_list"}:
        classes += " fs-widget-grid"
        if block.get("stack_mobile", True):
            classes += " fs-stack-mobile"
    return decorate_block(Section(*children, cls=classes, style=css), block, preview)
