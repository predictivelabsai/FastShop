"""Merchant-facing field metadata and complete insertion defaults."""

from __future__ import annotations

import copy
import uuid

from app.site_blocks import (
    BLOCK_TYPES,
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
        data = {"min": 0, "max": 2400 if bare == "max_width" else 300, "step": 1, "unit": "px"}
    elif bare == "body":
        kind = "textarea"
    return {
        "id": key,
        "label": _label(bare),
        "type": kind,
        "default": copy.deepcopy(default),
        "help": "",
        "dynamic_source": "image" if kind == "image" else "text" if not key.startswith("style.") and kind in {"text", "textarea", "richtext"} else None,
        **data,
    }


def _widget_field(key, default, spec):
    """Translate registry constraints into the established editor schema shape."""
    kind = spec.get("kind", "text")
    result = _field(key, default, choices=spec.get("options"))
    result["type"] = kind
    result["help"] = spec.get("help", "")
    if kind in {"range", "number", "int"}:
        result.update(type="range", min=spec["min"], max=spec["max"], step=1, unit=spec.get("unit", ""))
    elif kind == "resource_list":
        resource = spec["resource"]
        result.update(type="repeater", fields=[{"id": resource, "label": resource.capitalize(), "type": resource, "default": ""}], min=0, max=spec.get("max", 24), value_list=True)
    result["dynamic_source"] = "image" if kind == "image" else "text" if kind in {"text", "textarea", "richtext"} else None
    return result


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



LEGACY_PICKER = {
    "hero": ("hero", "Open with a large image or video"),
    "text": ("text", "Add an editorial text section"),
    "split": ("split", "Place an image beside your story"),
    "products": ("catalogue", "Feature a selection from your catalogue"),
    "facts": ("chart", "Present key figures at a glance"),
    "claims": ("shield", "Share reviewed statements with evidence"),
    "reviews": ("reviews", "Show a selection of customer reviews"),
    "articles": ("articles", "Highlight your latest articles"),
    "research": ("library", "Collect research and supporting resources"),
    "faq": ("question", "Answer common customer questions"),
    "team": ("people", "Introduce the people behind your business"),
    "contact": ("contact", "Let visitors send you a message"),
    "product": ("product-detail", "Show product information and purchasing options"),
    "references": ("references", "Link to studies and source material"),
    "embed": ("embed", "Include content from an external page"),
}


def _registry():

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
            label = spec.label
            category = (
                {
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
        fields = []
        for key in keys:
            if key == "children":
                continue
            definition = WIDGETS.get(kind)
            if definition and key == "items" and definition.item_schema:
                item_schema = definition.item_schema
                item_default = copy.deepcopy(item_schema["defaults"])
                item_keys = item_schema.get("editor_fields", list(item_schema["fields"]))
                item_fields = [_widget_field(k, item_default.get(k, ""), item_schema["fields"][k]) for k in item_keys]
                fields.append({
                    "id": "items", "label": "Entries", "type": "repeater",
                    "default": copy.deepcopy(default.get("items", [])),
                    "fields": item_fields, "item_fields": item_fields,
                    "item_default": item_default,
                    "theme_defaults": {theme: dict(item_default, theme=theme) for theme in item_schema.get("themes", ())},
                    "min": item_schema.get("min", 0), "max": item_schema.get("max", 200),
                    "help": "", "required": list(item_schema.get("required", ())), "fixed": False,
                })
                continue
            if definition and key in definition.fields:
                fields.append(_widget_field(key, default[key], definition.fields[key]))
                continue
            if key == "items":
                item_keys = ["heading", "body", "url", "image", "alt", "label", "value"]
                if themes:
                    item_keys = ["theme"] + item_keys
                required = spec.required_item_fields
                item_default = {
                    k: "Your content" if k not in {"url", "image"} else ("/" if k == "url" else "")
                    for k in item_keys
                }
                if themes:
                    item_default["theme"] = sorted(themes)[0]
                theme_defaults = {}
                for theme in themes:
                    entry = dict(item_default, theme=theme)
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
                        "min": 0,
                        "max": 200,
                        "help": "",
                        "required": list(required),
                        "fixed": False,
                    }
                )
                continue
            value = default.get(key, False if key in {"hidden", "subscription_preview"} else "")
            field = _field(
                key, value, choices=layouts if key == "layout" and layouts else None
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
            "icon": WIDGETS[kind].icon if kind in WIDGETS else LEGACY_PICKER[kind][0],
            "description": WIDGETS[kind].description if kind in WIDGETS else LEGACY_PICKER[kind][1],
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


def register_block_schema(kind, schema):
    """Add editor metadata for a separately registered, trusted block type."""
    if kind not in BLOCK_TYPES or kind in BLOCK_SCHEMAS:
        raise ValueError("Register a new block type before its editor schema.")
    saved = copy.deepcopy(schema)
    if saved["defaults"]["type"] != kind:
        raise ValueError("Schema defaults must use the registered block type.")
    saved["defaults"] = validate_block(saved["defaults"])
    BLOCK_SCHEMAS[kind] = saved
    from app.content import SECTION_TYPES
    SECTION_TYPES[kind] = BLOCK_TYPES[kind].label


def block_schema_registry():
    return copy.deepcopy(BLOCK_SCHEMAS)


def editor_schema():
    return block_schema_registry()


def default_block(kind, block_id=None):
    block = copy.deepcopy(BLOCK_SCHEMAS[kind]["defaults"])
    block["id"] = block_id or uuid.uuid4().hex
    return block
