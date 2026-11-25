"""Header and footer settings that merchants edit in the visual editor.

Each field id is a dotted path into ``Site.settings_json``. The built-in storefront
declares the shared copy it already reads (announcement, logos, tagline, contact
details, footer disclaimer), so nothing about how the storefront renders changes:
the editor simply writes the same keys as Settings > General. An optional theme pack
can register its own field list with :func:`register_shell`.

Values are read with schema defaults filling absent paths only, so merchant edits are
never overwritten, and saving writes the declared paths without touching unrelated
settings.
"""
from __future__ import annotations

import copy

from app.services import CommerceError

TEXT_TYPES = {"text", "textarea"}
_PROVIDERS = {}
_MISSING = object()

# The built-in storefront (app.site_ui.storefront) reads these settings keys.
STOREFRONT_FIELDS = (
    {"id": "announcement", "label": "Announcement bar", "type": "text", "group": "header",
     "help": "Shown above the header on every page. Leave empty to hide it."},
    {"id": "logo", "label": "Logo", "type": "image", "group": "header"},
    {"id": "logo_light", "label": "Logo on dark backgrounds", "type": "image", "group": "header"},
    {"id": "tagline", "label": "Tagline", "type": "textarea", "group": "footer"},
    {"id": "company", "label": "Company name", "type": "text", "group": "footer",
     "help": "Leave empty to use the store name."},
    {"id": "address", "label": "Address", "type": "textarea", "group": "footer"},
    {"id": "email", "label": "Contact email", "type": "text", "group": "footer"},
    {"id": "offer", "label": "Sign-up offer heading", "type": "text", "group": "footer",
     "default": "Stay curious."},
    {"id": "footer", "label": "Footer disclaimer", "type": "textarea", "group": "footer"},
)


def register_shell(pack: str, provider) -> None:
    """Register ``provider() -> list[field]`` for a theme pack."""
    _PROVIDERS[pack] = provider


def shell_fields(config: dict | None) -> list[dict]:
    from app.site_theme import theme_pack

    pack = theme_pack(config or {})
    provider = _PROVIDERS.get(pack)
    if provider:
        return copy.deepcopy(provider())
    if pack:
        return []  # A pack that declares no shell settings keeps its own chrome.
    return [{"max_length": 2000, "default": "", **field} for field in copy.deepcopy(STOREFRONT_FIELDS)]


def _get(data, path):
    node = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _set(data, path, value):
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def shell_values(config: dict | None, fields: list[dict] | None = None) -> dict:
    """Nested subset of the settings that the fields declare, defaults for absent paths."""
    fields = shell_fields(config) if fields is None else fields
    result = {}
    for field in fields:
        value = _get(config or {}, field["id"])
        _set(result, field["id"], copy.deepcopy(field.get("default", "")) if value is _MISSING else value)
    return result


def _clean(field, value):
    kind = field.get("type", "text")
    limit = field.get("max_length", 2000)
    if not isinstance(value, str) or len(value) > limit:
        raise CommerceError(f"{field['label']}: use text up to {limit} characters.")
    if kind == "image":
        if not value.strip():
            return ""
        from app.content import safe_url
        return safe_url(value.strip(), media=True)
    if kind not in TEXT_TYPES:
        raise CommerceError("Unsupported editable setting type.")
    return value


def apply_shell(config: dict, submitted: dict, fields: list[dict]) -> dict:
    """Return a copy of ``config`` with every declared, submitted path validated and saved.

    An absent setting stays absent while the submitted value equals its default, so
    opening and saving the panel never pins storefront fallbacks such as the store name.
    """
    if not isinstance(submitted, dict):
        raise CommerceError("Send header and footer settings as an object.")
    result = copy.deepcopy(config or {})
    for field in fields:
        value = _get(submitted, field["id"])
        if value is _MISSING:
            continue
        value = _clean(field, value)
        if _get(result, field["id"]) is _MISSING and value == field.get("default", ""):
            continue
        _set(result, field["id"], value)
    return result


def publish_shell(draft: dict, published: dict, fields: list[dict]) -> dict:
    """Copy the draft values of declared paths into the published settings."""
    result = copy.deepcopy(published or {})
    for field in fields:
        value = _get(draft or {}, field["id"])
        if value is not _MISSING:
            _set(result, field["id"], copy.deepcopy(value))
    return result
