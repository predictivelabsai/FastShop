"""Merchant-facing field metadata and complete insertion defaults."""

from __future__ import annotations

import copy
import uuid

from app.site_blocks import (
    BLOCK_TYPES,
    LIQUID_PRODUCT_LAYOUTS,
    LIQUID_PRODUCT_THEMES,
    TEXT_FIELDS,
    validate_block,
)
from app.site_widgets import WIDGETS


def _label(value):
    return (value.replace("_", " ").replace("-", " ").replace(".", " / ")
            .replace("flavor", "flavour").replace("color", "colour")
            .replace("center", "centre").capitalize())


def _options(values):
    return [{"value": value, "label": _label(value) or "Link"} for value in sorted(values)]


def _field(key, default="", *, choices=None):
    kind = "text"
    data = {}
    bare = key.removeprefix("style.")
    if choices is not None:
        kind, data = "select", {"options": _options(choices)}
    elif bare in {"image", "poster", "background_image"}:
        kind = "image"
    elif bare in {"video", "mobile_video"}:
        kind = "video"
    elif bare in {"url", "link"}:
        kind = "url"
    elif bare in {"background_colour", "text_colour"}:
        kind = "colour"
    elif bare in {"product", "collection"}:
        kind = bare
    elif bare in {"products", "collections"}:
        resource = "product" if bare == "products" else "collection"
        kind = "repeater"
        data = {
            "fields": [{"id": resource, "label": resource.capitalize(), "type": resource, "default": ""}],
            "min": 0,
            "max": 24,
            "value_list": True,
        }
    elif type(default) is bool:
        kind = "toggle"
    elif type(default) is int:
        kind = "range"
        minimum = 1 if bare in {"columns", "rating", "thickness", "limit"} else 0
        maximum = {
            "columns": 4,
            "gap": 120,
            "height": 600,
            "rating": 5,
            "thickness": 20,
            "limit": 24,
            "rounded": 100,
            "max_width": 2400,
        }.get(bare, 300)
        data = {
            "min": minimum,
            "max": maximum,
            "step": 1,
            "unit": "" if bare in {"columns", "rating", "limit"} else "px",
        }
    elif bare == "body":
        kind = "textarea"
    return {
        "id": key,
        "label": _label(bare),
        "type": kind,
        "default": copy.deepcopy(default),
        "help": "",
        **data,
    }


STYLE_DEFAULTS = {
    "padding_top": 24,
    "padding_bottom": 24,
    "margin_top": 0,
    "margin_bottom": 0,
    "background_colour": "#ffffff",
    "background_image": "",
    "text_colour": "#202223",
    "alignment": "left",
    "max_width": 1200,
    "hide_desktop": False,
    "hide_tablet": False,
    "hide_mobile": False,
    "anchor_id": "",
    "css_class": "",
}
STYLE_SCHEMA = [
    _field(
        "style." + key, value, choices={"left", "centre", "right"} if key == "alignment" else None
    )
    for key, value in STYLE_DEFAULTS.items()
]
ADVANCED_IDS = {
    "style.anchor_id",
    "style.css_class",
    "style.hide_desktop",
    "style.hide_tablet",
    "style.hide_mobile",
}
CHOICES = {
    "level": {"h1", "h2", "h3", "h4", "h5", "h6"},
    "alignment": {"left", "centre", "right"},
    "vertical_alignment": {"start", "center", "end", "stretch"},
    "aspect": {"auto", "1/1", "4/3", "16/9", "3/4"},
    "button_style": {"primary", "secondary", "link"},
}


def _registry():
    from app.site_liquid_editorial_content import EDITORIAL_FIELDS, editorial_documents
    from app.site_liquid_support_validation import REQUIRED, SCHEMAS

    schemas = {}
    for kind, spec in BLOCK_TYPES.items():
        default = {"id": "new-block", "type": kind, "version": 1}
        themes = set()
        layouts = set()
        insertable = True
        if kind in WIDGETS:
            default.update(copy.deepcopy(WIDGETS[kind][2]))
            label, category = WIDGETS[kind][:2]
            keys = list(WIDGETS[kind][2])
        else:
            label = spec.label.removeprefix("Liquid ")
            category = (
                "Theme sections"
                if kind.startswith("liquid_")
                else {
                    "hero": "Media",
                    "split": "Media",
                    "products": "Commerce",
                    "product": "Commerce",
                    "contact": "Forms",
                    "reviews": "Social proof",
                    "embed": "Advanced",
                }.get(kind, "Text")
            )
            default.update(
                heading="Your section",
                body="Add your content.",
                eyebrow="",
                button="Explore",
                link="/",
                url="",
                image="",
                alt="",
                items=[],
            )
            keys = sorted(TEXT_FIELDS) + ["items", "hidden", "subscription_preview"]
            if kind in LIQUID_PRODUCT_THEMES:
                themes = LIQUID_PRODUCT_THEMES[kind]
                layouts = LIQUID_PRODUCT_LAYOUTS.get(kind, {"light", "dark"})
                default["layout"] = sorted(layouts)[0]
                keys += ["layout"]
            if kind == "liquid_benefits":
                themes = {"cell", "shield", "exercise", "leaf", "molecule", "water"}
            if kind == "liquid_collection":
                themes = {"product", "spec", "flavor"}
            if kind in {"liquid_catalog", "liquid_reviews"}:
                from app.site_seed_liquid import documents

                themes = {
                    item["theme"]
                    for _, _, _, blocks in documents()
                    for section in blocks
                    if section["type"] == kind
                    for item in section.get("items", [])
                    if isinstance(item.get("theme"), str)
                }
                themes.add("Featured" if kind == "liquid_catalog" else "Customer")
            if kind.startswith("liquid_support_"):
                support = kind.removeprefix("liquid_support_")
                for key in REQUIRED[support]:
                    default.setdefault(key, "Your content" if key not in {"link", "url"} else "/")
                themes = set(SCHEMAS[support])
                insertable = False
            if kind == "liquid_editorial":
                # Fixed illustrations and chapter structures remain complete and editable.
                default = copy.deepcopy(editorial_documents()[0][3][0])
                default["id"] = "new-block"
                layouts = set(EDITORIAL_FIELDS)
                themes = set().union(*(set(fields) for fields in EDITORIAL_FIELDS.values()))
                keys += ["layout"]
                insertable = False
        fields = []
        for key in keys:
            if key == "children":
                continue
            if key == "items":
                if kind == "liquid_product_lifestyle":
                    continue
                item_keys = ["heading", "body", "url", "image", "alt", "label", "value"]
                if kind == "accordion":
                    item_keys = ["heading", "body"]
                elif kind == "image_gallery":
                    item_keys = ["image", "alt", "url"]
                elif kind == "icon_list":
                    item_keys = ["heading", "body", "theme"]
                    themes = {"check", "star", "arrow", "heart"}
                elif themes:
                    item_keys = ["theme"] + item_keys
                required = spec.required_item_fields
                item_default = {
                    k: "Your content" if k not in {"url", "image"} else ("/" if k == "url" else "")
                    for k in item_keys
                }
                if themes:
                    item_default["theme"] = sorted(themes)[0]
                if kind in {"liquid_reviews"}:
                    item_default["value"] = "5"
                if kind in LIQUID_PRODUCT_THEMES:
                    from app.site_blocks import LIQUID_PRODUCT_ITEM_FIELDS

                    for name in LIQUID_PRODUCT_ITEM_FIELDS.get(item_default.get("theme"), set()):
                        item_default.setdefault(name, "")
                    if item_default.get("theme") == "bundle":
                        item_default.update(value="1", body="0")
                    if item_default.get("theme") == "rating":
                        item_default.update(value="5", label="1")
                theme_defaults = {}
                for theme in themes:
                    entry = dict(item_default, theme=theme)
                    if kind in LIQUID_PRODUCT_THEMES:
                        if theme == "bundle":
                            entry.update(value="1", body="0")
                        elif theme == "rating":
                            entry.update(value="5", label="1")
                    if kind.startswith("liquid_support_"):
                        entry.update(
                            value="new-question",
                            heading="Your question",
                            label="Your link",
                            url="/",
                        )
                    theme_defaults[theme] = entry
                item_fields = [
                    _field(k, item_default[k], choices=themes if k == "theme" else None)
                    for k in item_keys
                ]
                fields.append(
                    {
                        "id": "items",
                        "label": "Entries",
                        "type": "repeater",
                        "default": copy.deepcopy(default.get("items", [])),
                        "fields": item_fields,
                        "item_fields": item_fields,
                        "item_default": item_default,
                        "theme_defaults": theme_defaults,
                        "min": len(default["items"]) if kind == "liquid_editorial" else 0,
                        "max": len(default["items"]) if kind == "liquid_editorial" else 200,
                        "help": "Keep the complete chapter structure."
                        if kind == "liquid_editorial"
                        else "",
                        "required": list(required),
                        "fixed": kind == "liquid_editorial",
                    }
                )
                continue
            value = default.get(key, False if key in {"hidden", "subscription_preview"} else "")
            field = _field(
                key, value, choices=layouts if key == "layout" and layouts else CHOICES.get(key)
            )
            if kind in {"richtext", "html"} and key == "body":
                field["type"] = "richtext"
                field["help"] = (
                    "Safe formatting only. Scripts and inline event handlers are removed."
                )
            if key == "collection":
                field["label"] = "Collection"
                field["help"] = "Choose a published catalogue collection."
            fields.append(field)
        fields += copy.deepcopy(STYLE_SCHEMA)
        schemas[kind] = {
            "label": label,
            "category": category,
            "icon": {
                "Layout": "layout",
                "Text": "type",
                "Media": "image",
                "Commerce": "shopping-bag",
                "Forms": "mail",
                "Social proof": "star",
                "Advanced": "code",
                "Theme sections": "layers",
            }[category],
            "description": f"Add and customise {label.lower()}.",
            "fields": fields,
            "groups": {
                "Content": [f["id"] for f in fields if not f["id"].startswith("style.")],
                "Style": [
                    f["id"]
                    for f in fields
                    if f["id"].startswith("style.") and f["id"] not in ADVANCED_IDS
                ],
                "Advanced": [f["id"] for f in fields if f["id"] in ADVANCED_IDS],
            },
            "accepts_children": kind == "container",
            "allowed_children": list(WIDGETS) if kind == "container" else [],
            "insertable": insertable,
            "defaults": validate_block(default),
        }
    return schemas


BLOCK_SCHEMAS = _registry()


def block_schema_registry():
    return copy.deepcopy(BLOCK_SCHEMAS)


def editor_schema():
    return block_schema_registry()


def default_block(kind, block_id=None):
    block = copy.deepcopy(BLOCK_SCHEMAS[kind]["defaults"])
    block["id"] = block_id or uuid.uuid4().hex
    return block
