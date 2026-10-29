"""Theme-pack shell settings (header, footer, consent) that merchants edit visually.

A theme pack registers a field list. Each field id is a dotted path into
``Site.settings_json``. Values are read with schema defaults filling absent
paths only, so merchant edits are never overwritten, and saving writes the
declared paths without touching unrelated settings.
"""
from __future__ import annotations

import copy
import importlib

from app.services import CommerceError

TEXT_TYPES = {"text", "textarea"}
_PROVIDERS = {}
_MISSING = object()


def register_shell(pack: str, provider) -> None:
    """Register ``provider() -> list[field]`` for a theme pack."""
    _PROVIDERS[pack] = provider


def shell_fields(config: dict | None) -> list[dict]:
    pack = str((config or {}).get("theme_pack", ""))
    if pack.isidentifier() and pack not in _PROVIDERS:
        # Convention: a pack's shell module is app/site_<pack>_shell.py and registers itself.
        try:
            importlib.import_module(f"app.site_{pack}_shell")
        except ModuleNotFoundError:
            pass
    provider = _PROVIDERS.get(pack)
    return copy.deepcopy(provider()) if provider else []


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


def _url(value):
    value = str(value).strip()
    if value in {"", "#"}:
        return value
    from app.content import safe_url
    return safe_url(value)


def _clean(field, value):
    kind = field.get("type", "text")
    limit = field.get("max_length", 2000)
    if kind == "list":
        items = value if isinstance(value, list) else str(value).split(",")
        items = [str(item).strip() for item in items if str(item).strip()]
        allowed = field.get("choices")
        if allowed and any(item not in allowed for item in items):
            raise CommerceError(f"{field['label']}: choose from {', '.join(allowed)}.")
        return items[:20]
    if kind == "repeater":
        if not isinstance(value, list) or len(value) > field.get("max", 20):
            raise CommerceError(f"{field['label']}: send at most {field.get('max', 20)} items.")
        rows = []
        for row in value:
            if not isinstance(row, dict):
                raise CommerceError(f"{field['label']}: each item needs its fields.")
            rows.append({sub["id"]: _clean(sub, row.get(sub["id"], sub.get("default", "")))
                         for sub in field["fields"]})
        return rows
    if not isinstance(value, str) or len(value) > limit:
        raise CommerceError(f"{field['label']}: use text up to {limit} characters.")
    if kind == "url":
        return _url(value)
    if kind not in TEXT_TYPES:
        raise CommerceError("Unsupported editable setting type.")
    return value


def apply_shell(config: dict, submitted: dict, fields: list[dict]) -> dict:
    """Return a copy of ``config`` with every declared, submitted path validated and saved."""
    if not isinstance(submitted, dict):
        raise CommerceError("Send header and footer settings as an object.")
    result = copy.deepcopy(config or {})
    for field in fields:
        value = _get(submitted, field["id"])
        if value is not _MISSING:
            _set(result, field["id"], _clean(field, value))
    return result


def publish_shell(draft: dict, published: dict, fields: list[dict]) -> dict:
    """Copy the draft values of declared paths into the published settings."""
    result = copy.deepcopy(published or {})
    for field in fields:
        value = _get(draft or {}, field["id"])
        if value is not _MISSING:
            _set(result, field["id"], copy.deepcopy(value))
    return result
